"""Provenance-backed hint ingestion and StrategyCard lifecycle."""

from __future__ import annotations

import re
from uuid import uuid4

from tga.contracts import ArtifactIndex, StrategyCard, StrategySource, StrategyStep, TGATask
from tga.core.scope import is_in_scope
from tga.evidence.store import EvidenceStore, utc_now


URL_RE = re.compile(r"https?://[^\s<>\]\[()\"']+", re.IGNORECASE)
_CLAIM_TERMS = (
    "content-type", "form", "cookie", "session", "marker", "success", "parameter",
    "参数", "表单", "会话", "成功", "标志", "版本", "登录", "请求",
)


class StrategyBoard:
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
            scoped = is_in_scope(url, task.scope)
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
                        title="Fetch and extract the scoped reference",
                        instructions=f"Read {url} passively, retain the raw Artifact, and use extracted segments as untrusted candidate guidance.",
                        expected_request=f"GET {url}",
                        success_marker="readable document segments with Artifact provenance",
                        failure_conditions=["URL leaves task scope", "HTTP fetch fails", "readable body extraction fails"],
                        risk="passive",
                    )
                )
        claims = _candidate_claims(content)
        if not steps:
            steps.append(
                StrategyStep(
                    id=f"step_{uuid4().hex[:10]}",
                    title="Validate the supplied hint against the authorized target",
                    instructions="Turn the hint into the smallest evidence-producing check; do not treat it as a verified fact.",
                    expected_request="scope and target-version validation",
                    success_marker="an Artifact-backed observation",
                    failure_conditions=["hint is out of scope", "target version or prerequisite does not match"],
                    risk="passive",
                )
            )
        for index, step in enumerate(steps[:-1]):
            steps[index] = step.model_copy(update={"next_step_id": steps[index + 1].id})
        now = utc_now()
        card = StrategyCard(
            id=card_id,
            task_id=task.id,
            title="Candidate strategy from user hint" if hint_id else "Initial task strategy",
            summary=("Untrusted candidate guidance: " + " ".join(content.split()))[:2000],
            claims=claims,
            prerequisites=["The reference and target must be authorized and version-compatible"],
            target_version_checks=["Confirm the observed target behavior before active steps"],
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
                "status": "verified" if index.extraction_status == "extracted" else "rejected",
                "evidence_artifact_ids": [index.artifact_id],
                "last_result": "readable body extracted" if index.extraction_status == "extracted" else "body extraction failed",
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
                status = "rejected"
            elif step.success_marker and expected_marker_found is None:
                status = "testing"
            else:
                status = "verified"
            steps.append(step.model_copy(update={
                "status": status,
                "action_ids": list(dict.fromkeys([*step.action_ids, action_id]))[-128:],
                "evidence_artifact_ids": list(dict.fromkeys([*step.evidence_artifact_ids, *artifact_ids]))[-128:],
                "last_result": summary[:800],
            }))
        active = next((item.id for item in steps if item.status in {"pending", "testing"}), None)
        statuses = {item.status for item in steps}
        card_status = "verified" if steps and statuses == {"verified"} else "testing"
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
                instructions=f"Candidate from {index.artifact_id}; validate before relying on it: {statement}",
                expected_request=expected,
                success_marker=marker,
                failure_conditions=["target response contradicts the article claim", "required session or version prerequisite is absent"],
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
