from collections import namedtuple
from urllib.parse import parse_qsl, unquote, urlsplit

from classic.migrations.backends.core.sqlite3 import SQLiteBackend

DatabaseURI = namedtuple(
    "DatabaseURI", "scheme username password hostname port database args"
)


class BadConnectionURI(Exception):
    """
    An invalid connection URI
    """


BACKENDS = {
    "sqlite": SQLiteBackend,
}


def get_backend_class(name):
    """
    Return the backend class registered for the given URI scheme.
    """
    try:
        return BACKENDS[name.lower()]
    except KeyError:
        raise BadConnectionURI(
            "Unrecognised database connection scheme %r" % name
        )


def parse_uri(s):
    """
    Examples::

        >>> parse_uri('postgres://fred:bassett@server:5432/fredsdatabase')
        ('postgres', 'fred', 'bassett', 'server', 5432, 'fredsdatabase', None)
        >>> parse_uri('mysql:///jimsdatabase')
        ('mysql', None, None, None, None, 'jimsdatabase', None, None)
        >>> parse_uri('odbc://user:password@server/database?DSN=dsn')
        ('odbc', 'user', 'password', 'server', None, 'database', {'DSN':'dsn'})
    """
    result = urlsplit(s)

    if not result.scheme:
        raise BadConnectionURI("No scheme specified in connection URI %r" % s)

    return DatabaseURI(
        scheme=result.scheme,
        username=(unquote(result.username) if result.username is not None else None),
        password=(unquote(result.password) if result.password is not None else None),
        hostname=result.hostname,
        port=result.port,
        database=result.path[1:] if result.path else None,
        args=dict(parse_qsl(result.query)),
    )
