from __future__ import annotations

import pytest

from scripts.migrate_solver_kali_capabilities import migrate_document


@pytest.mark.parametrize(
    ("binding", "expected"),
    [
        (
            {"profile_id": "profile", "allow_exec": True, "allow_session": False},
            {"profile_id": "profile", "capabilities": ["kali.exec"]},
        ),
        (
            {"profile_id": "profile", "allow_exec": True, "allow_session": True},
            {
                "profile_id": "profile",
                "capabilities": ["kali.exec", "kali.session"],
            },
        ),
        (
            {"profile_id": "profile", "allow_exec": False, "allow_session": False},
            None,
        ),
    ],
)
def test_one_shot_kali_binding_migration(binding, expected) -> None:
    assert migrate_document({"id": "solver", "kali": binding})["kali"] == expected


def test_migration_rejects_mixed_permission_models() -> None:
    with pytest.raises(ValueError, match="both legacy booleans and capabilities"):
        migrate_document({
            "kali": {
                "profile_id": "profile",
                "allow_exec": True,
                "capabilities": ["kali.exec"],
            }
        })
