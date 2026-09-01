import argparse
import logging
from collections.abc import Callable
from unittest.mock import MagicMock, patch

import pytest
from classic.migrations.cli import (
    _configure_logging,
    build_parser,
    cmd_apply,
    cmd_list,
    cmd_rollback,
    main,
    verbosity_levels,
)
from classic.migrations.migrations import Migration


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


def _run_cmd(
    func: Callable[[argparse.Namespace], int],
    args: argparse.Namespace,
    mock_migrator: MagicMock,
    mock_collection: MagicMock,
) -> int:
    with (
        patch("classic.migrations.cli._make_migrator", return_value=mock_migrator),
        patch("classic.migrations.cli._make_collection", return_value=mock_collection),
        patch("classic.migrations.cli.Settings"),
    ):
        return func(args)


class TestParser:
    def test_no_command_shows_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_apply_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply"])
        assert args.command == "apply"
        assert args.migration_name is None
        assert args.fake is False
        assert args.plan is False

    def test_apply_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply", "0001.init", "--fake", "--plan"])
        assert args.migration_name == "0001.init"
        assert args.fake is True
        assert args.plan is True

    def test_list_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert args.history is False

    def test_list_with_history(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["list", "--history"])
        assert args.history is True

    def test_rollback_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["rollback"])
        assert args.command == "rollback"
        assert args.migration_name is None
        assert args.fake is False
        assert args.plan is False

    def test_rollback_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["rollback", "0001.init", "--fake", "--plan"])
        assert args.migration_name == "0001.init"
        assert args.fake is True
        assert args.plan is True

    def test_verbosity(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply", "-v", "-v"])
        assert args.verbosity == 2

    def test_verbosity_maps_default_to_warning(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply"])
        assert args.verbosity == 0
        assert verbosity_levels[args.verbosity] == logging.WARNING

    def test_verbosity_maps_v_to_info(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply", "-v"])
        assert verbosity_levels[args.verbosity] == logging.INFO

    def test_verbosity_maps_vv_to_debug(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply", "-v", "-v"])
        assert verbosity_levels[args.verbosity] == logging.DEBUG

    def test_removed_commands_not_recognized(self) -> None:
        parser = build_parser()
        for command in ("develop", "reapply", "mark", "unmark", "new", "init"):
            with pytest.raises(SystemExit):
                parser.parse_args([command])


class TestCmdApply:
    def test_calls_to_apply_and_apply(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        _run_cmd(cmd_apply, args, mock_migrator, mock_collection)

        mock_collection.to_apply.assert_called_once_with([], target=None)
        mock_migrator.apply.assert_called_once_with(
            [],
            {},
            fake=False,
        )

    def test_passes_target_fake(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name="0001.init", fake=True, plan=False)
        _run_cmd(cmd_apply, args, mock_migrator, mock_collection)

        mock_collection.to_apply.assert_called_once_with([], target="0001.init")
        mock_migrator.apply.assert_called_once_with(
            [],
            {},
            fake=True,
        )

    def test_plan_does_not_apply(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=True)
        _run_cmd(cmd_apply, args, mock_migrator, mock_collection)

        mock_collection.to_apply.assert_called_once_with([], target=None)
        mock_migrator.apply.assert_not_called()


class TestCmdRollback:
    def test_calls_to_rollback_and_rollback(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=False)
        _run_cmd(cmd_rollback, args, mock_migrator, mock_collection)

        mock_collection.to_rollback.assert_called_once_with([], target=None)
        mock_migrator.rollback.assert_called_once_with(
            [],
            {},
            fake=False,
        )

    def test_passes_target_fake(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
    ) -> None:
        args = argparse.Namespace(migration_name="0001.init", fake=True, plan=False)
        _run_cmd(cmd_rollback, args, mock_migrator, mock_collection)

        mock_collection.to_rollback.assert_called_once_with([], target="0001.init")
        mock_migrator.rollback.assert_called_once_with(
            [],
            {},
            fake=True,
        )

    def test_plan_does_not_rollback(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(migration_name=None, fake=False, plan=True)
        _run_cmd(cmd_rollback, args, mock_migrator, mock_collection)

        mock_collection.to_rollback.assert_called_once_with([], target=None)
        mock_migrator.rollback.assert_not_called()


class TestCmdList:
    def test_calls_list_and_history(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(history=False)
        _run_cmd(cmd_list, args, mock_migrator, mock_collection)

        mock_collection.list.assert_called_once()
        mock_migrator.history.assert_called()

    def test_history_flag(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def make_migration(migration_id: str) -> Migration:
            return Migration(migration_id, f"/fake/{migration_id}.sql")

        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
        ]
        mock_collection.list.return_value = [
            make_migration("0001.a"),
            make_migration("0002.b"),
        ]
        args = argparse.Namespace(history=True)
        _run_cmd(cmd_list, args, mock_migrator, mock_collection)

        mock_collection.list.assert_called_once()

    def test_list_shows_status(
        self, mock_migrator: MagicMock, mock_collection: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def make_migration(migration_id: str) -> Migration:
            return Migration(migration_id, f"/fake/{migration_id}.sql")

        mock_migrator.history.return_value = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
        ]
        mock_collection.list.return_value = [
            make_migration("0001.a"),
            make_migration("0002.b"),
        ]
        args = argparse.Namespace(history=False)
        _run_cmd(cmd_list, args, mock_migrator, mock_collection)

        output = capsys.readouterr().out
        assert "0001.a" in output
        assert "0002.b" in output


class TestLogging:
    def test_configure_logging_sets_level(self) -> None:
        _configure_logging(2)
        assert logging.getLogger().level == logging.DEBUG

    def test_configure_logging_default_is_warning(self) -> None:
        _configure_logging(0)
        assert logging.getLogger().level == logging.WARNING


class TestMain:
    def test_main_no_command_returns_1(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with (
            patch("classic.migrations.cli._make_migrator"),
            patch("classic.migrations.cli._make_collection"),
            patch("classic.migrations.cli.Settings"),
        ):
            result = main([])
        assert result == 1

    def test_main_apply_returns_0(self) -> None:
        mock_migrator = MagicMock()
        mock_migrator.history.return_value = []
        mock_collection = MagicMock()
        mock_collection.to_apply.return_value = ({}, [])
        with (
            patch(
                "classic.migrations.cli._make_migrator",
                return_value=mock_migrator,
            ),
            patch(
                "classic.migrations.cli._make_collection",
                return_value=mock_collection,
            ),
            patch("classic.migrations.cli.Settings"),
        ):
            result = main(["apply"])
        assert result == 0

    def test_main_list_returns_0(self) -> None:
        mock_migrator = MagicMock()
        mock_migrator.history.return_value = []
        mock_collection = MagicMock()
        mock_collection.list.return_value = []
        with (
            patch(
                "classic.migrations.cli._make_migrator",
                return_value=mock_migrator,
            ),
            patch(
                "classic.migrations.cli._make_collection",
                return_value=mock_collection,
            ),
            patch("classic.migrations.cli.Settings"),
        ):
            result = main(["list"])
        assert result == 0
