import os
from pathlib import Path

import pytest
from classic.migrations.backends.base import Backend

from tests.backends.fake import FakeBackend

Backend.implementations["fake"] = FakeBackend

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key, value)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    return d