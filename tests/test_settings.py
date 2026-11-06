import os
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from classic.migrations.settings import Settings


class TestSourcesList:
    def test_split_by_os_pathsep(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SOURCES", f"a{os.pathsep}b")
        assert Settings(env_file=tmp_path / "none.env").sources_list == ["a", "b"]

    def test_windows_paths_with_semicolon(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("classic.migrations.settings.os.pathsep", ";")
        monkeypatch.setenv("SOURCES", r"C:\a\migrations;D:\b")
        settings = Settings(env_file=tmp_path / "none.env")
        assert settings.sources_list == [r"C:\a\migrations", r"D:\b"]

    def test_posix_paths_with_colon(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("classic.migrations.settings.os.pathsep", ":")
        monkeypatch.setenv("SOURCES", "/a/b:/c")
        assert Settings(env_file=tmp_path / "none.env").sources_list == ["/a/b", "/c"]

    def test_blank_items_and_spaces_dropped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        sep = os.pathsep
        monkeypatch.setenv("SOURCES", f" a {sep}{sep} {sep}b  {sep}")
        assert Settings(env_file=tmp_path / "none.env").sources_list == ["a", "b"]

    def test_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("SOURCES", raising=False)
        assert Settings(env_file=tmp_path / "none.env").sources_list == []

    def test_read_from_env_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("SOURCES", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text(f"SOURCES=x{os.pathsep}y\n", encoding="utf-8")
        assert Settings(env_file=env_file).sources_list == ["x", "y"]

    def test_environ_overrides_env_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SOURCES=fromfile\n", encoding="utf-8")
        monkeypatch.setenv("SOURCES", "fromenv")
        assert Settings(env_file=env_file).sources_list == ["fromenv"]


class TestLockTimeout:
    def _settings(self, tmp_path: Path) -> Settings:
        return Settings(env_file=tmp_path / "none.env")

    def test_missing_is_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("MIGRATIONS_LOCK_TIMEOUT", raising=False)
        assert self._settings(tmp_path).MIGRATIONS_LOCK_TIMEOUT is None

    def test_empty_is_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MIGRATIONS_LOCK_TIMEOUT", "")
        assert self._settings(tmp_path).MIGRATIONS_LOCK_TIMEOUT is None

    @pytest.mark.parametrize(
        ("raw", "expected"), [("0", 0.0), ("2.5", 2.5), ("30", 30.0)]
    )
    def test_parsed_as_float(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        raw: str,
        expected: float,
    ) -> None:
        monkeypatch.setenv("MIGRATIONS_LOCK_TIMEOUT", raw)
        assert expected == self._settings(tmp_path).MIGRATIONS_LOCK_TIMEOUT

    def test_invalid_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MIGRATIONS_LOCK_TIMEOUT", "abc")
        with pytest.raises(ValueError):
            self._settings(tmp_path).MIGRATIONS_LOCK_TIMEOUT  # noqa: B018


# ---- Mutant-killing tests for classic.migrations.settings ----


def test_skip_comment_with_equals(tmp_path: Path) -> None:
    """Kills mutmut_9 (or->and) and mutmut_10 (not line and -> not line or).

    Both mutants cause ``#KEY=VALUE`` to be parsed instead of skipped.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "#DATABASE_URL=sqlite:///test.db\nKEY=real\n",
        encoding="utf-8",
    )
    s = Settings(env_file=env_file)
    # The mutant parses the line with key ``#DATABASE_URL`` (the ``#`` is kept
    # by ``partition``), so the exact key must be asserted.
    assert "#DATABASE_URL" not in s._environ, (
        "Comment line with = was parsed instead of skipped"
    )
    assert "DATABASE_URL" not in s._environ
    assert s._environ.get("KEY") == "real"


def test_skip_comment_not_startswith_hash(tmp_path: Path) -> None:
    """Kills mutmut_13: ``startswith('#')`` -> ``startswith('XX#XX')``.

    With the mutant, neither a bare comment nor a ``#KEY=VALUE`` line is
    recognised as a comment, and both would be parsed into settings.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n#DB_URL=secret\nKEY=real\n", encoding="utf-8")
    s = Settings(env_file=env_file)
    # The mutant does not recognise ``#DB_URL=secret`` as a comment and parses
    # it with key ``#DB_URL``, so the exact key must be asserted.
    assert " comment" not in s._environ
    assert "#DB_URL" not in s._environ
    assert "DB_URL" not in s._environ
    assert s._environ.get("KEY") == "real"


def test_rpartition_for_multi_equals(tmp_path: Path) -> None:
    """Kills mutmut_18: ``partition('=')`` -> ``rpartition('=')``.

    For ``KEY=part1=part2``, ``partition`` yields
    ``("KEY", "=", "part1=part2")``
    but ``rpartition`` yields ``("KEY=part1", "=", "part2")``.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("KEY=part1=part2\n", encoding="utf-8")
    s = Settings(env_file=env_file)
    assert s._environ.get("KEY") == "part1=part2"


def test_utf8_env_file_non_ascii(tmp_path: Path) -> None:
    """Catch mutmut_5: ``encoding="utf-8"`` -> ``encoding=None``.

    Under ``encoding=None`` Python falls back to the locale encoding
    (cp1251 here), where the UTF-8 bytes of U+4E2D decode as three different
    cp1251 characters instead of raising; the parsed value must still equal
    the original character.
    """
    env_file = tmp_path / ".env"
    # U+4E2D (CJK Unified Ideograph) - not representable in cp1251.
    env_file.write_text("KEY=\u4e2d\n", encoding="utf-8")
    s = Settings(env_file=env_file)
    assert s._environ.get("KEY") == "\u4e2d"


def test_default_env_file_is_dot_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Kills mutmut_1: default ``".env"`` -> ``"XX.envXX"``.

    When ``Settings()`` is called without arguments, it must read the ``.env``
    file from the current working directory.
    """
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("SOURCES=from_default\n", encoding="utf-8")
    s = Settings()
    assert s.SOURCES == "from_default"


def test_env_file_opened_with_exact_utf8_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills mutmut_5 (``encoding=None``) and mutmut_7 (``encoding="UTF-8"``).

    ``None`` falls back to the locale encoding and ``"UTF-8"`` is the same
    codec as ``"utf-8"``, so the mutant is only observable through the exact
    value passed to ``Path.open``.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("KEY=1\n", encoding="utf-8")
    spy = MagicMock()
    spy.return_value = StringIO("KEY=1\n")
    monkeypatch.setattr(Path, "open", spy)
    Settings(env_file=env_file)
    assert spy.call_args is not None
    assert spy.call_args.kwargs.get("encoding") == "utf-8"
