import argparse
import io
import logging
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest

from classic.migrations.backends.base import Backend
from classic.migrations.cli import CLI, _is_driver_error, main, verbosity_levels
from classic.migrations.exceptions import (
    BadConnectionURI,
    BadMigration,
    MigrationConflict,
    MigrationLockError,
    MigrationPending,
    MissingSource,
    NoMigration,
)
from classic.migrations.migrations import Migration
from tests.backends.fake import FakeDatabaseError

if TYPE_CHECKING:
    from classic.migrations.migrations import MigrationsCollection
    from classic.migrations.migrator import Migrator


@pytest.fixture
def mock_migrator() -> MagicMock:
    mock = MagicMock()
    mock.history.return_value = []
    return mock


@pytest.fixture
def mock_collection() -> MagicMock:
    mock = MagicMock()
    mock.list.return_value = []
    mock.to_apply.return_value = ({}, [])
    mock.to_rollback.return_value = ({}, [])
    return mock


@pytest.fixture
def cli(
    mock_migrator: MagicMock,
    mock_collection: MagicMock,
) -> CLI:
    return CLI(
        collection=cast("MigrationsCollection", mock_collection),
        migrator=cast("Migrator", mock_migrator),
    )


def _patch_builders(
    mock_migrator: MagicMock,
    mock_collection: MagicMock,
) -> list[Any]:
    return [
        patch.object(CLI, "_make_migrator", return_value=mock_migrator),
        patch.object(CLI, "_make_collection", return_value=mock_collection),
    ]


def _make_migration(migration_id: str) -> Migration:
    return Migration(migration_id, f"/fake/{migration_id}.sql")


class TestParser:
    def test_no_command_has_no_func(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_apply_defaults(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["apply"])
        assert args.command == "apply"
        assert args.migration_name is None
        assert args.fake is False
        assert args.plan is False

    def test_apply_full(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["apply", "0001.init", "--fake", "--plan"])
        assert args.migration_name == "0001.init"
        assert args.fake is True
        assert args.plan is True

    def test_list_defaults(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert args.history is False

    def test_list_with_history(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["list", "--history"])
        assert args.history is True

    def test_rollback_defaults(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["rollback"])
        assert args.command == "rollback"
        assert args.migration_name is None
        assert args.fake is False
        assert args.plan is False

    def test_rollback_full(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["rollback", "0001.init", "--fake", "--plan"])
        assert args.migration_name == "0001.init"
        assert args.fake is True
        assert args.plan is True

    def test_verbosity_counts(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["apply", "-v", "-v"])
        assert args.verbosity == 2

    def test_verbosity_default_is_zero(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["apply"])
        assert args.verbosity == 0
        assert verbosity_levels[args.verbosity] == logging.WARNING

    def test_verbosity_maps_v_to_info(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["apply", "-v"])
        assert verbosity_levels[args.verbosity] == logging.INFO

    def test_verbosity_maps_vv_to_debug(self, cli: CLI) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["apply", "-v", "-v"])
        assert verbosity_levels[args.verbosity] == logging.DEBUG

    def test_removed_commands_not_recognized(self, cli: CLI) -> None:
        parser = cli.build_parser()
        for command in ("develop", "reapply", "mark", "unmark", "new", "init"):
            with pytest.raises(SystemExit):
                parser.parse_args([command])

    def test_unknown_command_exits_with_error(
        self,
        cli: CLI,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = cli.build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["nope"])
        assert exc_info.value.code == 2
        assert "invalid choice" in capsys.readouterr().err


class TestCmdApply:
    def test_calls_to_apply_and_apply(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        result = cli._apply(args)

        assert result == 0
        mock_collection.to_apply.assert_called_once_with([], target=None, fake=False)
        mock_migrator.apply.assert_called_once_with([], {}, fake=False)

    def test_opens_and_closes_migrator_context(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        cli._apply(args)

        mock_migrator.__enter__.assert_called_once()
        mock_migrator.__exit__.assert_called_once()

    def test_passes_target_fake(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name="0001.init", fake=True, plan=False)
        cli._apply(args)

        mock_collection.to_apply.assert_called_once_with(
            [],
            target="0001.init",
            fake=True,
        )
        mock_migrator.apply.assert_called_once_with([], {}, fake=True)

    def test_plan_does_not_apply(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=True)
        cli._apply(args)

        mock_collection.to_apply.assert_called_once_with([], target=None, fake=False)
        mock_migrator.apply.assert_not_called()
        assert "ID,SOURCE" in capsys.readouterr().out


class TestCmdRollback:
    def test_calls_to_rollback_and_rollback(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        result = cli._rollback(args)

        assert result == 0
        mock_collection.to_rollback.assert_called_once_with([], target=None, fake=False)
        mock_migrator.rollback.assert_called_once_with([], {}, fake=False)

    def test_opens_and_closes_migrator_context(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        cli._rollback(args)

        mock_migrator.__enter__.assert_called_once()
        mock_migrator.__exit__.assert_called_once()

    def test_passes_target_fake(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name="0001.init", fake=True, plan=False)
        cli._rollback(args)

        mock_collection.to_rollback.assert_called_once_with(
            [],
            target="0001.init",
            fake=True,
        )
        mock_migrator.rollback.assert_called_once_with([], {}, fake=True)

    def test_plan_does_not_rollback(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=True)
        cli._rollback(args)

        mock_collection.to_rollback.assert_called_once_with([], target=None, fake=False)
        mock_migrator.rollback.assert_not_called()
        assert "ID,SOURCE" in capsys.readouterr().out


class TestCmdList:
    def test_calls_list_and_history(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(history=False)
        result = cli._list(args)

        assert result == 0
        mock_migrator.history.assert_called_once()
        mock_collection.list.assert_called_once()

    def test_list_shows_applied_and_unapplied(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
        ]
        mock_collection.list.return_value = [
            _make_migration("0001.a"),
            _make_migration("0002.b"),
        ]
        args = argparse.Namespace(history=False)
        cli._list(args)

        output = capsys.readouterr().out
        assert "STATUS,ID,SOURCE" in output
        assert "A,0001.a" in output
        assert "U,0002.b" in output

    def test_history_flag_shows_only_applied(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
        ]
        mock_collection.list.return_value = [
            _make_migration("0001.a"),
            _make_migration("0002.b"),
        ]
        args = argparse.Namespace(history=True)
        cli._list(args)

        output = capsys.readouterr().out
        assert "0001.a" in output
        assert "0002.b" not in output

    def test_rolled_back_migration_is_unapplied(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
            ("0001.a", "2020-01-02 00:00:00", "ROLLED_BACK"),
        ]
        mock_collection.list.return_value = [_make_migration("0001.a")]
        args = argparse.Namespace(history=False)
        cli._list(args)

        assert "U,0001.a" in capsys.readouterr().out

    def test_pending_migration_shown_as_p(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
            ("0002.b", "2020-01-02 00:00:00", "PENDING"),
        ]
        mock_collection.list.return_value = [
            _make_migration("0001.a"),
            _make_migration("0002.b"),
            _make_migration("0003.c"),
        ]
        cli._list(argparse.Namespace(history=False))

        output = capsys.readouterr().out
        assert "A,0001.a" in output
        assert "P,0002.b" in output
        assert "U,0003.c" in output

    def test_pending_after_rollback_is_p_not_u(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
            ("0001.a", "2020-01-02 00:00:00", "PENDING"),
        ]
        mock_collection.list.return_value = [_make_migration("0001.a")]
        cli._list(argparse.Namespace(history=False))

        assert "P,0001.a" in capsys.readouterr().out

    def test_history_flag_shows_applied_and_pending(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
            ("0002.b", "2020-01-02 00:00:00", "PENDING"),
            ("0003.c", "2020-01-03 00:00:00", "APPLIED"),
            ("0003.c", "2020-01-04 00:00:00", "ROLLED_BACK"),
        ]
        mock_collection.list.return_value = [
            _make_migration("0001.a"),
            _make_migration("0002.b"),
            _make_migration("0003.c"),
            _make_migration("0004.d"),
        ]
        cli._list(argparse.Namespace(history=True))

        output = capsys.readouterr().out
        assert "A,0001.a" in output
        assert "P,0002.b" in output
        assert "0003.c" not in output
        assert "0004.d" not in output

    def test_colors_pending_yellow_on_tty(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "PENDING"),
        ]
        mock_collection.list.return_value = [_make_migration("0001.a")]
        fake_stdout = _FakeTty()
        monkeypatch.setattr(sys, "stdout", fake_stdout)
        cli._list(argparse.Namespace(history=False))

        assert "\033[93mP\033[0m" in fake_stdout.getvalue()

    def test_empty_list_prints_header_only(
        self,
        cli: CLI,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(history=False)
        cli._list(args)

        output = capsys.readouterr().out
        assert "STATUS,ID,SOURCE" in output
        assert "0001" not in output

    def test_colors_status_on_tty(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
        ]
        mock_collection.list.return_value = [
            _make_migration("0001.a"),
            _make_migration("0002.b"),
        ]
        fake_stdout = _FakeTty()
        monkeypatch.setattr(sys, "stdout", fake_stdout)
        args = argparse.Namespace(history=False)
        cli._list(args)

        output = fake_stdout.getvalue()
        assert "\033[92mA\033[0m" in output
        assert "\033[91mU\033[0m" in output

    def test_prints_source_dir(
        self,
        cli: CLI,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        migration = Migration("0001.a", "/fake/0001.a.sql", source_dir="migrations")
        mock_collection.list.return_value = [migration]
        args = argparse.Namespace(history=False)
        cli._list(args)

        assert "migrations" in capsys.readouterr().out


def _get_subparser(
    parser: argparse.ArgumentParser,
    name: str,
) -> argparse.ArgumentParser | None:
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if choices and name in choices:
            return choices[name]
    return None


def _get_action(subparser: argparse.ArgumentParser | None, dest: str) -> Any:
    if subparser is None:
        return None
    for action in subparser._actions:
        if action.dest == dest:
            return action
    return None


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


class TestLogging:
    def test_configure_logging_default_is_warning(self, cli: CLI) -> None:
        cli._configure_logging(0)
        assert logging.getLogger().level == logging.WARNING

    def test_configure_logging_v_is_info(self, cli: CLI) -> None:
        cli._configure_logging(1)
        assert logging.getLogger().level == logging.INFO

    def test_configure_logging_vv_is_debug(self, cli: CLI) -> None:
        cli._configure_logging(2)
        assert logging.getLogger().level == logging.DEBUG

    def test_verbosity_clamped_to_max(self, cli: CLI) -> None:
        cli(["apply", "-v", "-v", "-v"])
        assert logging.getLogger().level == logging.DEBUG

    def test_verbosity_clamped_to_min(self, cli: CLI) -> None:
        cli(["apply"])
        assert logging.getLogger().level == logging.WARNING


class TestMain:
    def test_main_no_command_returns_1(
        self,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            result = main([])
        assert result == 1
        assert "usage" in capsys.readouterr().err

    def test_main_apply_returns_0(
        self,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            result = main(["apply"])
        assert result == 0
        mock_migrator.apply.assert_called_once_with([], {}, fake=False)

    def test_main_rollback_returns_0(
        self,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            result = main(["rollback"])
        assert result == 0
        mock_migrator.rollback.assert_called_once_with([], {}, fake=False)

    def test_main_list_returns_0(
        self,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            result = main(["list"])
        assert result == 0
        mock_collection.list.assert_called_once()
        assert "STATUS,ID,SOURCE" in capsys.readouterr().out

    def test_main_unknown_exception_propagates(
        self,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        mock_collection.to_apply.side_effect = ValueError("boom")
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1], pytest.raises(ValueError, match="boom"):
            main(["apply"])

    def test_main_uses_same_migrator_and_collection(
        self,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            main(["apply"])
        mock_migrator.history.assert_called_once()


@pytest.fixture
def empty_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Окружение без настроек и без .env в текущем каталоге."""
    for name in (
        "SOURCES",
        "DATABASE_DRIVER",
        "DATABASE_PORT",
        "DATABASE_NAME",
        "DATABASE_HOST",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "MIGRATIONS_LOCK_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestLazyCreation:
    def test_constructor_does_not_build(self, empty_env: Path) -> None:
        CLI()

    def test_constructor_does_not_call_builders(self) -> None:
        with (
            patch.object(CLI, "_make_collection", side_effect=AssertionError),
            patch.object(CLI, "_make_migrator", side_effect=AssertionError),
        ):
            CLI()

    def test_help_works_in_empty_env(
        self,
        empty_env: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with (
            patch.object(CLI, "_make_collection", side_effect=AssertionError),
            patch.object(CLI, "_make_migrator", side_effect=AssertionError),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["--help"])
        assert exc_info.value.code == 0
        assert "usage" in capsys.readouterr().out

    def test_no_command_in_empty_env_returns_1(
        self,
        empty_env: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with (
            patch.object(CLI, "_make_collection", side_effect=AssertionError),
            patch.object(CLI, "_make_migrator", side_effect=AssertionError),
        ):
            assert main([]) == 1
        assert "usage" in capsys.readouterr().err

    def test_unknown_command_in_empty_env_exits_2(self, empty_env: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["nope"])
        assert exc_info.value.code == 2

    def test_builders_cache_result(
        self,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
    ) -> None:
        cli = CLI()
        with (
            patch.object(
                CLI, "_make_migrator", return_value=mock_migrator
            ) as make_migrator,
            patch.object(
                CLI, "_make_collection", return_value=mock_collection
            ) as make_collection,
        ):
            assert cli.migrator is mock_migrator
            assert cli.migrator is mock_migrator
            assert cli.collection is mock_collection
            assert cli.collection is mock_collection
        make_migrator.assert_called_once()
        make_collection.assert_called_once()

    @pytest.mark.parametrize("command", ["apply", "list", "rollback"])
    def test_collection_built_before_migrator(
        self,
        empty_env: Path,
        command: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch.object(CLI, "_make_migrator") as make_migrator:
            result = main([command])
        assert result == 1
        make_migrator.assert_not_called()
        assert capsys.readouterr().err == "error: SOURCES is not set\n"


class TestConfigErrors:
    def test_sources_not_set(
        self,
        empty_env: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["apply"]) == 1
        err = capsys.readouterr().err
        assert "error: SOURCES is not set" in err
        assert "Traceback" not in err

    def test_driver_not_set(
        self,
        empty_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("SOURCES", str(empty_env))
        assert main(["apply"]) == 1
        assert capsys.readouterr().err == "error: DATABASE_DRIVER is not set\n"

    def test_port_not_integer(
        self,
        empty_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("SOURCES", str(empty_env))
        monkeypatch.setenv("DATABASE_DRIVER", "sqlite3")
        monkeypatch.setenv("DATABASE_PORT", "abc")
        assert main(["apply"]) == 1
        err = capsys.readouterr().err
        assert err.startswith("error: DATABASE_PORT must be an integer")
        assert "abc" in err
        assert "Traceback" not in err

    def test_lock_timeout_not_number(
        self,
        empty_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("SOURCES", str(empty_env))
        monkeypatch.setenv("DATABASE_DRIVER", "sqlite3")
        monkeypatch.setenv("MIGRATIONS_LOCK_TIMEOUT", "abc")
        assert main(["apply"]) == 1
        err = capsys.readouterr().err
        assert err.startswith(
            "error: MIGRATIONS_LOCK_TIMEOUT must be a non-negative number",
        )
        assert "Traceback" not in err

    def test_lock_timeout_negative(
        self,
        empty_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("SOURCES", str(empty_env))
        monkeypatch.setenv("DATABASE_DRIVER", "sqlite3")
        monkeypatch.setenv("MIGRATIONS_LOCK_TIMEOUT", "-1")
        assert main(["apply"]) == 1
        err = capsys.readouterr().err
        assert err.startswith(
            "error: MIGRATIONS_LOCK_TIMEOUT must be a non-negative number",
        )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(None, None), ("", None), ("0", 0.0), ("2.5", 2.5)],
    )
    def test_lock_timeout_passed_to_migrator(
        self,
        empty_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        raw: str | None,
        expected: float | None,
    ) -> None:
        monkeypatch.setenv("DATABASE_DRIVER", "sqlite3")
        if raw is not None:
            monkeypatch.setenv("MIGRATIONS_LOCK_TIMEOUT", raw)
        with patch("classic.migrations.cli.Migrator") as migrator_cls:
            CLI()._make_migrator()
        assert migrator_cls.call_args.kwargs["lock_timeout"] == expected


class TestErrorHandling:
    @pytest.mark.parametrize(
        "error_class",
        [
            BadConnectionURI,
            BadMigration,
            MigrationConflict,
            NoMigration,
            MigrationLockError,
            MigrationPending,
            MissingSource,
        ],
    )
    @pytest.mark.parametrize("command", ["apply", "rollback"])
    def test_library_error_from_collection(
        self,
        cli: CLI,
        mock_collection: MagicMock,
        error_class: type[Exception],
        command: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        if command == "apply":
            method = mock_collection.to_apply
        else:
            method = mock_collection.to_rollback
        method.side_effect = error_class("Migration 'x' not found")

        assert cli([command]) == 1
        captured = capsys.readouterr()
        assert captured.err == "error: Migration 'x' not found\n"
        assert "Traceback" not in captured.err

    @pytest.mark.parametrize("error_class", [MigrationLockError, MissingSource])
    def test_library_error_from_list(
        self,
        cli: CLI,
        mock_collection: MagicMock,
        error_class: type[Exception],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_collection.list.side_effect = error_class("boom")
        assert cli(["list"]) == 1
        assert capsys.readouterr().err == "error: boom\n"

    def test_library_error_from_migrator_enter(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.__enter__.side_effect = MigrationLockError("locked")
        assert cli(["apply"]) == 1
        assert capsys.readouterr().err == "error: locked\n"

    def test_empty_message_uses_class_name(
        self,
        cli: CLI,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_collection.to_apply.side_effect = NoMigration()
        assert cli(["apply"]) == 1
        assert capsys.readouterr().err == "error: NoMigration\n"

    def test_sqlite_driver_error(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.history.side_effect = sqlite3.OperationalError("no such table: t")
        assert cli(["list"]) == 1
        assert capsys.readouterr().err == "error: OperationalError: no such table: t\n"

    def test_fake_driver_error(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.apply.side_effect = FakeDatabaseError("bad")
        assert cli(["apply"]) == 1
        assert capsys.readouterr().err == "error: FakeDatabaseError: bad\n"

    def test_vv_prints_traceback(
        self,
        cli: CLI,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_collection.to_apply.side_effect = NoMigration("nope")
        assert cli(["apply", "-vv"]) == 1
        err = capsys.readouterr().err
        assert "Traceback" in err
        assert err.rstrip("\n").endswith("error: nope")

    def test_v_does_not_print_traceback(
        self,
        cli: CLI,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_collection.to_apply.side_effect = NoMigration("nope")
        assert cli(["apply", "-v"]) == 1
        assert "Traceback" not in capsys.readouterr().err

    def test_unknown_exception_propagates(
        self,
        cli: CLI,
        mock_collection: MagicMock,
    ) -> None:
        mock_collection.to_apply.side_effect = RuntimeError("bug")
        with pytest.raises(RuntimeError, match="bug"):
            cli(["apply"])

    def test_end_to_end_unknown_target(
        self,
        empty_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source = empty_env / "migrations"
        source.mkdir()
        (source / "0001.init.sql").write_text(
            "CREATE TABLE t(id INTEGER);\n", encoding="utf-8"
        )
        monkeypatch.setenv("SOURCES", str(source))
        monkeypatch.setenv("DATABASE_DRIVER", "sqlite3")
        monkeypatch.setenv("DATABASE_NAME", str(empty_env / "db.sqlite"))

        assert main(["apply", "nonexistent"]) == 1
        err = capsys.readouterr().err
        assert err == "error: Migration 'nonexistent' not found\n"
        assert "Traceback" not in err


class TestIsDriverError:
    def test_detects_error_via_driver_Error_attr(self) -> None:
        class _MockDriver:
            __name__ = "mock_error"
            DatabaseError = ValueError
            Error = TypeError

        class _MockBackend:
            driver = _MockDriver

        with patch.dict(Backend.implementations, {"mock_error": _MockBackend}):
            assert _is_driver_error(TypeError("test")) is True
            assert _is_driver_error(ValueError("test")) is False
            assert _is_driver_error(RuntimeError("test")) is False

    def test_detects_error_without_Error_attr(self) -> None:
        class _MockDriver:
            __name__ = "mock_no_error"
            DatabaseError = TypeError

        class _MockBackend:
            driver = _MockDriver

        with patch.dict(Backend.implementations, {"mock_no_error": _MockBackend}):
            assert _is_driver_error(TypeError("test")) is True
            assert _is_driver_error(ValueError("test")) is False


class TestParserProgAndHelp:
    def test_parser_prog_is_migrations(self, cli: CLI) -> None:
        parser = cli.build_parser()
        assert parser.prog == "migrations"

    def test_verbosity_help(self, cli: CLI) -> None:
        parser = cli.build_parser()
        action = _get_action(_get_subparser(parser, "apply"), "verbosity")
        assert action is not None
        assert action.help == (
            "Verbose output. Use multiple times to increase level of verbosity"
        )

    def test_apply_help_text(self, cli: CLI) -> None:
        parser = cli.build_parser()
        assert "Apply migrations" in parser.format_help()

    def test_apply_migration_name_help(self, cli: CLI) -> None:
        parser = cli.build_parser()
        action = _get_action(_get_subparser(parser, "apply"), "migration_name")
        assert action is not None
        assert action.help == "Apply migrations up to and including this one"

    def test_apply_fake_help(self, cli: CLI) -> None:
        parser = cli.build_parser()
        action = _get_action(_get_subparser(parser, "apply"), "fake")
        assert action is not None
        assert action.help == (
            "Only create history records, without running the migrations"
        )

    def test_apply_plan_help(self, cli: CLI) -> None:
        parser = cli.build_parser()
        action = _get_action(_get_subparser(parser, "apply"), "plan")
        assert action is not None
        assert action.help == (
            "Do not apply migrations, only list those that could be applied"
        )

    def test_list_help_text(self, cli: CLI) -> None:
        parser = cli.build_parser()
        assert "List all available and applied migrations" in parser.format_help()

    def test_list_history_help(self, cli: CLI) -> None:
        parser = cli.build_parser()
        action = _get_action(_get_subparser(parser, "list"), "history")
        assert action is not None
        assert action.help == "Show only applied and pending migrations"

    def test_rollback_help_text(self, cli: CLI) -> None:
        parser = cli.build_parser()
        assert "Rollback migrations" in parser.format_help()

    def test_rollback_migration_name_help(self, cli: CLI) -> None:
        parser = cli.build_parser()
        action = _get_action(_get_subparser(parser, "rollback"), "migration_name")
        assert action is not None
        assert action.help == "Rollback migrations up to and including this one"

    def test_rollback_fake_help(self, cli: CLI) -> None:
        parser = cli.build_parser()
        action = _get_action(_get_subparser(parser, "rollback"), "fake")
        assert action is not None
        assert action.help == (
            "Only create history records, without running the migrations"
        )

    def test_rollback_plan_help(self, cli: CLI) -> None:
        parser = cli.build_parser()
        action = _get_action(_get_subparser(parser, "rollback"), "plan")
        assert action is not None
        assert action.help == (
            "Do not rollback migrations, only list those that could be rolled back"
        )

    def test_help_text_no_xx_prefix(self, cli: CLI) -> None:
        """Help texts must not have 'XX' prefix/suffix (mutmut_44, _96, _122)."""
        help_text = cli.build_parser().format_help()
        assert "XXApply" not in help_text
        assert "XXList" not in help_text
        assert "XXRollback" not in help_text


class TestLoggingFormat:
    def test_configure_logging_format(self, cli: CLI) -> None:
        with patch.object(logging, "basicConfig") as mock_basic_config:
            cli._configure_logging(0)
        mock_basic_config.assert_called_once_with(
            format="%(levelname)s %(name)s: %(message)s",
            level=logging.WARNING,
            force=True,
        )


class TestMakeMigrator:
    def test_passes_all_params(
        self,
        empty_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SOURCES", str(empty_env))
        monkeypatch.setenv("DATABASE_DRIVER", "sqlite3")
        monkeypatch.setenv("DATABASE_HOST", "myhost")
        monkeypatch.setenv("DATABASE_PORT", "6543")
        monkeypatch.setenv("DATABASE_NAME", "mydb")
        monkeypatch.setenv("DATABASE_USER", "myuser")
        monkeypatch.setenv("DATABASE_PASSWORD", "mypass")
        monkeypatch.setenv("MIGRATIONS_LOCK_TIMEOUT", "3.5")
        monkeypatch.setenv("MIGRATIONS_TABLE", "my_migrations")
        monkeypatch.setenv("MIGRATIONS_SCHEMA", "my_schema")
        monkeypatch.setenv("OLD_MIGRATIONS_SCHEMA", "my_old_schema")

        with patch("classic.migrations.cli.Migrator") as mig_cls:
            CLI()._make_migrator()

        mig_cls.assert_called_once_with(
            driver="sqlite3",
            db_host="myhost",
            db_port=6543,
            db_name="mydb",
            db_user="myuser",
            db_pass="mypass",  # noqa: S106
            lock_timeout=3.5,
            migration_table="my_migrations",
            migration_schema="my_schema",
            versions_schema="my_old_schema",
        )


class TestPrintMigrations:
    def test_shows_source_dir(
        self,
        cli: CLI,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        migrations = [
            Migration("0001.a", "/fake/0001.a.sql", source_dir="dir1"),
            Migration("0002.b", "/fake/0002.b.sql", source_dir="dir2"),
        ]
        cli._print_migrations(migrations)
        output = capsys.readouterr().out
        lines = output.splitlines()
        assert lines[1] == "0001.a,dir1"
        assert lines[2] == "0002.b,dir2"

    def test_empty_source_dir_shows_empty(
        self,
        cli: CLI,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        migrations = [Migration("0001.a", "/fake/0001.a.sql", source_dir=None)]
        cli._print_migrations(migrations)
        output = capsys.readouterr().out
        lines = output.splitlines()
        cells = lines[1].split(",")
        assert cells == ["0001.a", ""]

    def test_csv_header(
        self,
        cli: CLI,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        migrations = [Migration("0001.a", "/fake/0001.a.sql")]
        cli._print_migrations(migrations)
        output = capsys.readouterr().out
        lines = output.splitlines()
        assert lines[0] == "ID,SOURCE"


class TestCmdListEmptySource:
    def test_empty_source_dir_shows_empty_cell(
        self,
        cli: CLI,
        mock_migrator: MagicMock,
        mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_migrator.history.return_value = []
        mock_collection.list.return_value = [
            _make_migration("0001.a"),
        ]
        args = argparse.Namespace(history=False)
        cli._list(args)

        output = capsys.readouterr().out
        lines = output.splitlines()
        cells = lines[1].split(",")
        assert cells == ["U", "0001.a", ""]

    def test_with_object_missing_source_dir(
        self,
        cli: CLI,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Object without ``source_dir`` (mutmut_9, _12 in _print_migrations)."""

        class _Obj:
            id = "no_src"

        cli._print_migrations([_Obj()])
        output = capsys.readouterr().out
        lines = output.splitlines()
        assert lines[0] == "ID,SOURCE"
        cells = lines[1].split(",")
        assert cells == ["no_src", ""]
