"""Provenance-backed hint ingestion and StrategyCard lifecycle."""

from __future__ import annotations

import re
from uuid import uuid4

from tga.contracts import ArtifactIndex, StrategyCard, StrategySource, StrategyStep, TGATask
from tga.network_policy import authorize_url
from tga.evidence.store import EvidenceStore, utc_now


URL_RE = re.compile(r"https?://[^\s<>\]\[()\"']+", re.IGNORECASE)
_CLAIM_TERMS = (
    "content-type", "form", "cookie", "session", "marker", "success", "parameter",
    "参数", "表单", "会话", "成功", "标志", "版本", "登录", "请求",
)


class StrategyService:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def ensure_from_hint(self, *, task: TGATask, hint_id: str | None, content: str) -> StrategyCard:
        urls = list(dict.fromkeys(URL_RE.findall(content)))
        fingerprint = "|".join([hint_id or "goal", *urls, " ".join(content.casefold().split())[:300]])
        card_id = f"strategy_{uuid4().hex[:12]}"
        for current in self.store.list_strategy_cards(task.id):
            source_hints = {source.hint_id for source in current.sources}
            source_urls = {source.url for source in current.sources}
            if (hint_id and hint_id in source_hints) or (urls and set(urls) <= source_urls):
                return current

        sources: list[StrategySource] = []
        steps: list[StrategyStep] = []
        for url in urls:
            try:
                authorize_url(url, task.execution_policy.network, resolve_dns=False)  # type: ignore[union-attr]
                scoped = True
            except (PermissionError, ValueError):
                scoped = False
            sources.append(
                StrategySource(
                    hint_id=hint_id,
                    url=url,
                    extraction_status="not_requested" if scoped else "blocked_out_of_scope",
                )
            )
            if scoped:
                steps.append(
                    StrategyStep(
                        id=f"step_{uuid4().hex[:10]}",
                        title="抓取并提取已授权范围内的参考内容",
                        instructions=f"以被动方式读取 {url}，保留原始证据产物，并将提取的片段视为未经验证的候选指引。",
                        expected_request=f"GET {url}",
                        success_marker="已提取带证据来源的可读文档片段",
                        failure_conditions=["URL 超出任务范围", "HTTP 获取失败", "无法提取可读正文"],
                        risk="passive",
                    )
                )
        claims = _candidate_claims(content)
        if not steps:
            steps.append(
                StrategyStep(
                    id=f"step_{uuid4().hex[:10]}",
                    title="在已授权目标上验证用户提供的提示",
                    instructions="将提示转化为最小的证据产出检查；不要把它当作已经验证的事实。",
                    expected_request="范围和目标版本校验",
                    success_marker="获得由证据产物支持的观察结果",
                    failure_conditions=["提示超出授权范围", "目标版本或前置条件不匹配"],
                    risk="passive",
                )
            )
        for index, step in enumerate(steps[:-1]):
            steps[index] = step.model_copy(update={"next_step_id": steps[index + 1].id})
        now = utc_now()
        card = StrategyCard(
            id=card_id,
            task_id=task.id,
            title="来自用户提示的候选策略" if hint_id else "初始任务策略",
            summary=("未经验证的候选指引：" + " ".join(content.split()))[:2000],
            claims=claims,
            prerequisites=["参考内容和目标必须已获授权且版本兼容"],
            target_version_checks=["执行主动步骤前确认目标实际行为"],
            sources=sources or [StrategySource(hint_id=hint_id, source_refs=[fingerprint[:120]])],
            steps=steps,
            active_step_id=steps[0].id,
            created_at=now,
            updated_at=now,
        )
        return self.store.upsert_strategy_card(card)

    def attach_index(self, *, card: StrategyCard, url: str, index: ArtifactIndex) -> StrategyCard:
        sources = []
        found = False
        for source in card.sources:
            if source.url == url:
                found = True
                sources.append(source.model_copy(update={
                    "artifact_id": index.artifact_id,
                    "extraction_status": index.extraction_status,
                    "source_refs": [segment.ref for segment in index.segments[:16]],
                }))
            else:
                sources.append(source)
        if not found:
            sources.append(StrategySource(url=url, artifact_id=index.artifact_id, extraction_status=index.extraction_status, source_refs=[item.ref for item in index.segments[:16]]))

        claims = list(dict.fromkeys([*card.claims, *_candidate_claims(index.summary)]))[:24]
        steps = list(card.steps)
        fetch_step = next((item for item in steps if item.expected_request == f"GET {url}"), None)
        if fetch_step:
            updated = fetch_step.model_copy(update={
                "status": "succeeded" if index.extraction_status == "extracted" else "failed",
                "evidence_artifact_ids": [index.artifact_id],
                "last_result": "已提取可读正文" if index.extraction_status == "extracted" else "正文提取失败",
            })
            steps = [updated if item.id == fetch_step.id else item for item in steps]

        if index.extraction_status == "extracted":
            derived = _derived_steps(index)
            known_titles = {item.title.casefold() for item in steps}
            steps.extend(item for item in derived if item.title.casefold() not in known_titles)
        for position, step in enumerate(steps[:-1]):
            if not step.next_step_id:
                steps[position] = step.model_copy(update={"next_step_id": steps[position + 1].id})
        active = next((item.id for item in steps if item.status in {"pending", "testing"}), None)
        updated_card = card.model_copy(update={
            "sources": sources,
            "claims": claims,
            "steps": steps,
            "active_step_id": active,
            "status": "testing" if active else card.status,
            "updated_at": utc_now(),
        })
        return self.store.upsert_strategy_card(updated_card)

    def record_action(
        self, *, card_id: str | None, step_id: str | None, action_id: str, artifact_ids: list[str],
        succeeded: bool, summary: str, expected_marker_found: bool | None = None,
    ) -> StrategyCard | None:
        if not card_id or not step_id:
            return None
        card = self.store.get_strategy_card(card_id)
        if card is None:
            return None
        steps: list[StrategyStep] = []
        for step in card.steps:
            if step.id != step_id:
                steps.append(step)
                continue
            if not succeeded or expected_marker_found is False:
                status = "failed"
            elif step.success_marker and expected_marker_found is None:
                status = "testing"
            else:
                status = "succeeded"
            steps.append(step.model_copy(update={
                "status": status,
                "action_ids": list(dict.fromkeys([*step.action_ids, action_id]))[-128:],
                "evidence_artifact_ids": list(dict.fromkeys([*step.evidence_artifact_ids, *artifact_ids]))[-128:],
                "last_result": summary[:800],
            }))
        active = next((item.id for item in steps if item.status in {"pending", "testing"}), None)
        statuses = {item.status for item in steps}
        card_status = "succeeded" if steps and statuses == {"succeeded"} else "testing"
        updated = card.model_copy(update={"steps": steps, "active_step_id": active, "status": card_status, "updated_at": utc_now()})
        return self.store.upsert_strategy_card(updated)


def _candidate_claims(text: str) -> list[str]:
    values = []
    for sentence in re.split(r"(?<=[.!?。！？；;])\s*|\n+", text):
        clean = " ".join(sentence.split()).strip(" -#*\t")
        folded = clean.casefold()
        if 8 <= len(clean) <= 500 and any(term in folded for term in _CLAIM_TERMS):
            values.append(clean)
    return list(dict.fromkeys(values))[:24]


def _derived_steps(index: ArtifactIndex) -> list[StrategyStep]:
    text = "\n".join(item.text for item in index.segments[:24])
    statements = _candidate_claims(text)
    steps: list[StrategyStep] = []
    for statement in statements[:8]:
        folded = statement.casefold()
        risk = "active" if any(term in folded for term in ("post", "payload", "exploit", "提交", "请求")) else "passive"
        marker = _extract_marker(statement)
        expected = "explicit form request" if any(term in folded for term in ("content-type", "form", "表单")) else "evidence-producing request"
        steps.append(
            StrategyStep(
                id=f"step_{uuid4().hex[:10]}",
                title=(statement[:117] + "...") if len(statement) > 120 else statement,
                instructions=f"来自 {index.artifact_id} 的候选内容；使用前必须验证：{statement}",
                expected_request=expected,
                success_marker=marker,
                failure_conditions=["目标响应与参考内容的主张相矛盾", "缺少所需的会话或版本前置条件"],
                risk=risk,
            )
        )
    return steps


def _extract_marker(value: str) -> str:
    for pattern in (r"`([^`]{1,80})`", r"(?:marker|标志|返回|出现)\s*[:：=]?\s*([\w{}.-]{2,80})"):
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return match.group(1)[:300]
    return ""
