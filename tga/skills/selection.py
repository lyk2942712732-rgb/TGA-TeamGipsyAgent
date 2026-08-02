"""Task-aware Skill ranking and immutable bundle construction."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from tga.modes import TaskMode
from tga.domain.skills.models import SkillSnapshot, TaskCommonSkillSnapshot
from tga.skills.retrieval import RegistrySkillRetriever, SkillRetrievalQuery, SkillRetriever


# The schema-v6 Task Common Skill snapshot admits at most two Skills.  The
# selector shares that bound so a third Skill is rejected at the entry point
# instead of being silently dropped later.
MAX_SELECTED_SKILLS = 2
MAX_SKILL_BODY_CHARS = 12_000
MAX_SKILL_CONTEXT_CHARS = 24_000
CUSTOM_SKILL_PRIORITY = 1_000


@dataclass(frozen=True)
class SkillSelectionRequest:
    mode: TaskMode
    goal: str
    task_id: str
    prompt: str = ""
    file_names: tuple[str, ...] = ()
    mode_config: dict[str, Any] | None = None
    available_capabilities: tuple[str, ...] = ()
    selected_skill_names: tuple[str, ...] | None = None

    @property
    def search_text(self) -> str:
        config = json.dumps(self.mode_config or {}, ensure_ascii=False, sort_keys=True)
        return " ".join([self.goal, self.prompt, *self.file_names, config]).strip()


class SkillSelector:
    """Select and freeze task guidance without depending on task persistence."""

    selector_id = "task-skill-selector-v1"

    def __init__(self, retriever: SkillRetriever | None = None) -> None:
        self.retriever = retriever or RegistrySkillRetriever()

    def select(
        self, request: SkillSelectionRequest, *, created_at: str
    ) -> TaskCommonSkillSnapshot:
        requested_names = request.selected_skill_names
        if requested_names is not None:
            if len(requested_names) > MAX_SELECTED_SKILLS:
                raise ValueError(f"manual Skill selection supports at most {MAX_SELECTED_SKILLS} items")
            if len(set(requested_names)) != len(requested_names):
                raise ValueError("manual Skill selection contains duplicate names")
        inferred_tags = _infer_tags(request)
        candidates = self.retriever.retrieve(SkillRetrievalQuery(
            mode=request.mode,
            text=request.search_text,
            tags=tuple(sorted(inferred_tags)),
            required_capabilities=request.available_capabilities,
            limit=64,
        ))
        available = set(request.available_capabilities)
        ranked: list[tuple[int, str, Any, list[str]]] = []
        if requested_names is not None:
            by_name = {candidate.skill.name: candidate for candidate in candidates}
            unavailable = [name for name in requested_names if name not in by_name]
            if unavailable:
                raise ValueError(
                    "selected Skills do not exist or are incompatible with the task scene: "
                    + ", ".join(unavailable)
                )
            for index, name in enumerate(requested_names):
                candidate = by_name[name]
                missing = sorted(set(candidate.skill.capabilities) - available)
                if missing:
                    raise ValueError(
                        f"selected Skill {name} requires unavailable capabilities: {', '.join(missing)}"
                    )
                ranked.append((10_000 - index, name, candidate, ["用户在创建任务时手动选择"]))
            return self._snapshot(
                request, ranked, selection_mode="manual", created_at=created_at
            )

        for candidate in candidates:
            skill = candidate.skill
            missing = sorted(set(skill.capabilities) - available)
            if missing:
                continue
            matched_tags = sorted(inferred_tags.intersection(skill.tags))
            lexical = _lexical_overlap(request.search_text, skill.name, skill.tags, skill.body)
            if not matched_tags and lexical < 2 and not candidate.retrieval_reasons:
                continue
            origin_priority = CUSTOM_SKILL_PRIORITY if candidate.origin == "custom" else 0
            score = (
                100
                + candidate.retrieval_score
                + len(matched_tags) * 160
                + lexical * 12
                + origin_priority
            )
            reasons = [*candidate.retrieval_reasons]
            if origin_priority:
                reasons.append("用户自定义 Skill 优先")
            reasons.extend(f"任务特征匹配：{tag}" for tag in matched_tags)
            if lexical:
                reasons.append(f"任务文本相关词匹配：{lexical}")
            ranked.append((score, skill.name, candidate, list(dict.fromkeys(reasons))))
        ranked.sort(key=lambda item: (-item[0], item[1]))

        return self._snapshot(
            request, ranked, selection_mode="automatic", created_at=created_at
        )

    def _snapshot(
        self,
        request: SkillSelectionRequest,
        ranked: list[tuple[int, str, Any, list[str]]],
        *,
        selection_mode: str,
        created_at: str,
    ) -> TaskCommonSkillSnapshot:

        selected: list[SkillSnapshot] = []
        used_chars = 0
        for score, _, candidate, reasons in ranked:
            if len(selected) >= MAX_SELECTED_SKILLS or used_chars >= MAX_SKILL_CONTEXT_CHARS:
                break
            raw_body = candidate.skill.body.strip()
            remaining = MAX_SKILL_CONTEXT_CHARS - used_chars
            body_limit = min(MAX_SKILL_BODY_CHARS, remaining)
            body = raw_body[:body_limit].rstrip()
            if not body:
                continue
            if len(body) < len(raw_body):
                marker = "\n\n[Skill 正文因上下文预算已截断]"
                body = raw_body[:max(0, body_limit - len(marker))].rstrip() + marker
            selected.append(SkillSnapshot(
                name=candidate.skill.name,
                version=candidate.skill.version,
                origin=candidate.origin,
                modes=tuple(candidate.skill.modes),
                required_capabilities=tuple(candidate.skill.capabilities),
                tags=tuple(candidate.skill.tags),
                body=body,
                content_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                selection_reasons=tuple(reasons[:16]),
            ))
            used_chars += len(body)
        return TaskCommonSkillSnapshot(
            task_id=request.task_id,
            selector=f"task-common:{self.selector_id}:{self.retriever.retriever_id}:{selection_mode}"[:128],
            skills=tuple(selected),
            total_chars=sum(len(item.body) for item in selected),
            created_at=created_at,
            legacy_import=False,
        )


def _infer_tags(request: SkillSelectionRequest) -> set[str]:
    text = request.search_text.casefold()
    tags: set[str] = set()
    aliases = {
        "web": ("http", "https", "web", "api", "form", "login", "cookie", "网页", "接口", "登录"),
        "recon": ("http://", "https://", "recon", "surface", "endpoint", "route", "侦察", "端点", "路由"),
        "sqli": ("sql", "sqli", "注入"),
        "idor": ("idor", "越权", "authorization"),
        "upload": ("upload", "上传"),
        "auth": ("auth", "login", "session", "认证", "登录", "会话"),
        "crypto": ("crypto", "cipher", "rsa", "aes", "密码", "加密", "解密"),
        "encoding": ("base64", "encoding", "decode", "编码", "解码"),
        "binary": ("binary", "elf", "pe32", "exe", "dll", "二进制", "pwn"),
        "metadata": ("metadata", "strings", "元数据", "字符串"),
        "source": ("source", "repository", "code", "源码", "仓库", "代码"),
        "incident-response": ("incident", "breach", "应急", "入侵", "事件响应"),
        "timeline": ("timeline", "时间线"),
        "ioc": ("ioc", "indicator", "威胁指标"),
        "forensics": ("forensic", "memory dump", "pcap", "取证", "流量", "内存"),
    }
    for tag, words in aliases.items():
        if any(_contains_alias(text, word) for word in words):
            tags.add(tag)
    subtype = str((request.mode_config or {}).get("subtype") or "").casefold()
    subtype_tags = {
        "web": {"web", "recon"},
        "pwn": {"binary"},
        "reverse": {"binary", "metadata"},
        "crypto": {"crypto", "encoding"},
        "forensics": {"forensics", "metadata"},
    }
    tags.update(subtype_tags.get(subtype, set()))
    for name in request.file_names:
        suffix = PurePath(name).suffix.casefold()
        if suffix in {".exe", ".dll", ".elf", ".so", ".bin"}:
            tags.update({"binary", "metadata"})
        elif suffix in {".py", ".js", ".ts", ".php", ".java", ".c", ".cpp", ".go", ".rs"}:
            tags.add("source")
        elif suffix in {".pcap", ".pcapng", ".evtx", ".dmp"}:
            tags.update({"forensics", "incident-response"})
    if request.mode == "incident_response":
        tags.add("incident-response")
    return tags


def _lexical_overlap(text: str, name: str, tags: list[str], body: str) -> int:
    stop_words = {"and", "the", "this", "that", "with", "from", "mode", "auto", "true", "false"}
    query_tokens = {
        token for token in re.findall(r"[a-z0-9_-]{3,}", text.casefold())
        if token not in stop_words
    }
    document = " ".join([name, *tags, body[:2_000]]).casefold()
    return min(sum(1 for token in query_tokens if token in document), 20)


def _contains_alias(text: str, alias: str) -> bool:
    if re.fullmatch(r"[a-z0-9_-]+", alias):
        return re.search(rf"(?<![a-z0-9_-]){re.escape(alias)}(?![a-z0-9_-])", text) is not None
    return alias in text
