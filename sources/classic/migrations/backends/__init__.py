from classic.migrations.backends.base import Backend
from classic.migrations.backends.core.sqlite3 import SQLiteBackend

try:
    from classic.migrations.backends.core.psycopg import (
        PsycopgBackend as _PsycopgBackend,
    )
except ImportError:
    pass
else:
    PsycopgBackend = _PsycopgBackend

__all__ = [
    "Backend",
    "SQLiteBackend",
]
