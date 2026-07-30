"""Frozen model-visible tool surface for one Solver assignment."""

from pydantic import BaseModel, ConfigDict

from tga.runtime.tooling.catalog.definitions import ToolCatalogEntry


class SolverToolManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    solver_id: str
    solver_definition_id: str
    intent_id: str | None = None
    entries: tuple[ToolCatalogEntry, ...]
    policy_fingerprints: tuple[str, ...]

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(item.provider_tool_name for item in self.entries)

    def get(self, provider_name: str) -> ToolCatalogEntry | None:
        return next(
            (item for item in self.entries if item.provider_tool_name == provider_name),
            None,
        )


__all__ = ["SolverToolManifest"]
