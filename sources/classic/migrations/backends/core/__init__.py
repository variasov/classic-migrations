from classic.migrations.backends.core.sqlite3 import SQLiteBackend

try:
    from classic.migrations.backends.core.psycopg import PsycopgBackend
except ImportError:
    pass

__all__ = [
    "SQLiteBackend",
]
