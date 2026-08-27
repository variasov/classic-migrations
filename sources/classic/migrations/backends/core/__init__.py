from classic.migrations.backends.sqlite3 import SQLiteBackend

try:
    from classic.migrations.backends.psycopg import (
        PsycopgBackend as _PsycopgBackend,
    )
except ImportError:
    pass
else:
    PsycopgBackend = _PsycopgBackend

__all__ = [
    "SQLiteBackend",
]
