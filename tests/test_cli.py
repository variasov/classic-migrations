import argparse
import io
import logging
import sys
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest
from classic.migrations.cli import CLI, main, verbosity_levels
from classic.migrations.exceptions import InvalidArgument
from classic.migrations.migrations import Migration

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
        self, cli: CLI, capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = cli.build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["nope"])
        assert exc_info.value.code == 2
        assert "invalid choice" in capsys.readouterr().err


class TestCmdApply:
    def test_calls_to_apply_and_apply(
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        result = cli._apply(args)

        assert result == 0
        mock_collection.to_apply.assert_called_once_with([], target=None)
        mock_migrator.apply.assert_called_once_with([], {}, fake=False)

    def test_opens_and_closes_migrator_context(
        self, cli: CLI, mock_migrator: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        cli._apply(args)

        mock_migrator.__enter__.assert_called_once()
        mock_migrator.__exit__.assert_called_once()

    def test_passes_target_fake(
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name="0001.init", fake=True, plan=False)
        cli._apply(args)

        mock_collection.to_apply.assert_called_once_with([], target="0001.init")
        mock_migrator.apply.assert_called_once_with([], {}, fake=True)

    def test_plan_does_not_apply(
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=True)
        cli._apply(args)

        mock_collection.to_apply.assert_called_once_with([], target=None)
        mock_migrator.apply.assert_not_called()
        assert "ID,SOURCE" in capsys.readouterr().out


class TestCmdRollback:
    def test_calls_to_rollback_and_rollback(
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        result = cli._rollback(args)

        assert result == 0
        mock_collection.to_rollback.assert_called_once_with([], target=None)
        mock_migrator.rollback.assert_called_once_with([], {}, fake=False)

    def test_opens_and_closes_migrator_context(
        self, cli: CLI, mock_migrator: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        cli._rollback(args)

        mock_migrator.__enter__.assert_called_once()
        mock_migrator.__exit__.assert_called_once()

    def test_passes_target_fake(
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name="0001.init", fake=True, plan=False)
        cli._rollback(args)

        mock_collection.to_rollback.assert_called_once_with([], target="0001.init")
        mock_migrator.rollback.assert_called_once_with([], {}, fake=True)

    def test_plan_does_not_rollback(
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=True)
        cli._rollback(args)

        mock_collection.to_rollback.assert_called_once_with([], target=None)
        mock_migrator.rollback.assert_not_called()
        assert "ID,SOURCE" in capsys.readouterr().out


class TestCmdList:
    def test_calls_list_and_history(
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(history=False)
        result = cli._list(args)

        assert result == 0
        mock_migrator.history.assert_called_once()
        mock_collection.list.assert_called_once()

    def test_list_shows_applied_and_unapplied(
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
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
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
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
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
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

    def test_empty_list_prints_header_only(
        self, cli: CLI, capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(history=False)
        cli._list(args)

        output = capsys.readouterr().out
        assert "STATUS,ID,SOURCE" in output
        assert "0001" not in output

    def test_colors_status_on_tty(
        self, cli: CLI, mock_migrator: MagicMock, mock_collection: MagicMock,
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
        self, cli: CLI, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        migration = Migration("0001.a", "/fake/0001.a.sql", source_dir="migrations")
        mock_collection.list.return_value = [migration]
        args = argparse.Namespace(history=False)
        cli._list(args)

        assert "migrations" in capsys.readouterr().out


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
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            result = main([])
        assert result == 1
        assert "usage" in capsys.readouterr().err

    def test_main_apply_returns_0(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            result = main(["apply"])
        assert result == 0
        mock_migrator.apply.assert_called_once_with([], {}, fake=False)

    def test_main_rollback_returns_0(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            result = main(["rollback"])
        assert result == 0
        mock_migrator.rollback.assert_called_once_with([], {}, fake=False)

    def test_main_list_returns_0(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            result = main(["list"])
        assert result == 0
        mock_collection.list.assert_called_once()
        assert "STATUS,ID,SOURCE" in capsys.readouterr().out

    def test_main_invalid_argument_exits_2(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_collection.to_apply.side_effect = InvalidArgument("boom")
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1], pytest.raises(SystemExit) as exc_info:
            main(["apply"])
        assert exc_info.value.code == 2
        assert "boom" in capsys.readouterr().err

    def test_main_uses_same_migrator_and_collection(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        patches = _patch_builders(mock_migrator, mock_collection)
        with patches[0], patches[1]:
            main(["apply"])
        mock_migrator.history.assert_called_once()
