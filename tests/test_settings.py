import os
from pathlib import Path

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
