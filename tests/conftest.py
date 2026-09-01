import os
from pathlib import Path

import pytest
from classic.migrations.backends.base import Backend

from tests.backends.fake import FakeBackend

Backend.implementations["fake"] = FakeBackend

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for raw_line in _env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def get_credentials(prefix: str) -> dict[str, str | None]:
    """Return ``DATABASE_*`` credentials for the given environment prefix."""

    def _value(field: str) -> str | None:
        return os.environ.get(f"{prefix}DATABASE_{field}")

    return {
        "host": _value("HOST"),
        "port": _value("PORT"),
        "name": _value("NAME"),
        "user": _value("USER"),
        "password": _value("PASSWORD"),
    }


@pytest.fixture
def source(tmp_path: Path) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    return d
