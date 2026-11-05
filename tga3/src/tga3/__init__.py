"""TGA3 blackboard-first dual-agent runtime."""

__all__ = ["EntryKind", "RunState", "TGA3Config"]
__version__ = "0.1.0"


def __getattr__(name):
    # Lightweight command hooks must not construct control-plane schemas.
    if name == "TGA3Config":
        from .config import TGA3Config
        return TGA3Config
    if name in {"EntryKind", "RunState"}:
        from . import domain
        return getattr(domain, name)
    raise AttributeError(name)
