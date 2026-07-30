from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
REQUIRED_DOCUMENTS = {
    "README.md": ("TaskOrchestrator", "v5 read-only", "SQLite"),
    "docs/architecture/OPERATIONS.md": ("pause", "backup", "SSE"),
    "docs/architecture/SECURITY_MODEL.md": ("Hint", "Skill", "RAG", "Approval"),
    "docs/architecture/RECOVERY.md": ("lease", "replay", "rollback"),
    "docs/architecture/RAG.md": ("global", "workspace", "task", "solver"),
    "docs/architecture/FRONTEND_WORKBENCH.md": ("Team Explorer", "Intent", "Replay"),
    "docs/architecture/LEGACY_CLEANUP_REPORT.md": ("MemoryEntry", "StrategyCard", "deprecated"),
    "docs/performance/BASELINE.md": ("Snapshot", "SSE", "10k"),
    "docs/RELEASE_NOTES_V6.md": ("schema v6", "compatibility", "verification"),
}


def test_release_document_set_records_required_operational_contracts() -> None:
    missing: list[str] = []
    for relative, terms in REQUIRED_DOCUMENTS.items():
        path = ROOT / relative
        if not path.is_file():
            missing.append(f"missing {relative}")
            continue
        content = path.read_text(encoding="utf-8").casefold()
        for term in terms:
            if term.casefold() not in content:
                missing.append(f"{relative} does not document {term}")
    assert missing == []


def test_api_routes_do_not_import_sqlite_or_legacy_evidence_store() -> None:
    violations: list[str] = []
    for path in (ROOT / "apps" / "api" / "routes").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "sqlite3" or name.startswith("tga.evidence.store") for name in names):
                violations.append(f"{path.name}: {names}")
    assert violations == []


def test_current_frontend_components_do_not_read_legacy_runtime_authority() -> None:
    current = ROOT / "apps" / "web" / "src" / "features"
    violations: list[str] = []
    for path in current.rglob("*.tsx"):
        if ".test." in path.name:
            continue
        source = path.read_text(encoding="utf-8")
        for term in ("active_solver_id", "strategy_cards", "runtime.memory", "solvers[0]"):
            if term in source:
                violations.append(f"{path.relative_to(current)}: {term}")
    assert violations == []
