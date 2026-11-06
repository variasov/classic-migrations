# Copyright 2015 Oliver Cope
# Copyright 2026 Sergey Variasov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Database backend registration and exports."""

from classic.migrations.backends.base import Backend
from classic.migrations.backends.sqlite3 import SQLiteBackend

try:
    from classic.migrations.backends.psycopg import (
        PsycopgBackend as _PsycopgBackend,
    )
except ImportError:
    pass
else:
    PsycopgBackend = _PsycopgBackend

try:
    from classic.migrations.backends.oracle import (
        OracleBackend as _OracleBackend,
    )
except ImportError:
    pass
else:
    OracleBackend = _OracleBackend

try:
    from classic.migrations.backends.pymysql import (
        PyMySQLBackend as _PyMySQLBackend,
    )
except ImportError:
    pass
else:
    PyMySQLBackend = _PyMySQLBackend

try:
    from classic.migrations.backends.pymssql import (
        PyMSSQLBackend as _PyMSSQLBackend,
    )
except ImportError:
    pass
else:
    PyMSSQLBackend = _PyMSSQLBackend

__all__ = [
    "Backend",
    "SQLiteBackend",
]
