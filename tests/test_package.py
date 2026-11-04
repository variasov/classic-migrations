import importlib.metadata
import subprocess
import sys

import pytest

import classic.migrations


def test_version_matches_package_metadata() -> None:
    assert classic.migrations.__version__ == importlib.metadata.version(
        "classic-migrations"
    )


@pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="typing_extensions is a declared dependency only on Python < 3.11",
)
def test_import_without_typing_extensions() -> None:
    code = (
        "import sys\n"
        "sys.modules['typing_extensions'] = None\n"
        "import classic.migrations\n"
        "import classic.migrations.migrator\n"
        "import classic.migrations.backends.base\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed args, no untrusted input
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
