"""Standalone entry point for the installed schema-v5 to schema-v6 migrator."""

from tga.migrations.schema_v5_to_v6 import (
    MigrationError,
    main,
    migrate_database,
    verify_database,
)

__all__ = ["MigrationError", "main", "migrate_database", "verify_database"]


if __name__ == "__main__":
    raise SystemExit(main())
