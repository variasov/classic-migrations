import argparse
from collections.abc import Callable
from unittest.mock import MagicMock, patch

import pytest
from classic.migrations.cli import (
    build_parser,
    cmd_apply,
    cmd_develop,
    cmd_list,
    cmd_mark,
    cmd_new,
    cmd_reapply,
    cmd_rollback,
    cmd_unmark,
    main,
)


@pytest.fixture
def mock_migrations() -> MagicMock:
    mock = MagicMock()
    mock.list.return_value = []
    mock.new.return_value = "/fake/path/20250101_01_abcde_add.sql"
    return mock


def _run_cmd(
    func: Callable[[argparse.Namespace], int],
    args: argparse.Namespace,
    mock_migrations: MagicMock,
) -> int:
    with (
        patch("classic.migrations.cli._make_migrations", return_value=mock_migrations),
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
        assert args.match is None
        assert args.revision is None
        assert args.all is False
        assert args.force is False
        assert args.one is False
        assert args.skip_hash_check is False

    def test_apply_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["apply", "-m", "0001", "-r", "0002", "-a", "-f", "-1", "--skip-hash-check"]
        )
        assert args.match == "0001"
        assert args.revision == "0002"
        assert args.all is True
        assert args.force is True
        assert args.one is True
        assert args.skip_hash_check is True

    def test_develop_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["develop"])
        assert args.command == "develop"
        assert args.n == 1
        assert args.skip_hash_check is False

    def test_develop_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["develop", "-n", "3", "--skip-hash-check"])
        assert args.n == 3
        assert args.skip_hash_check is True

    def test_list_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.match is None

    def test_list_with_match(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["list", "-m", "0001"])
        assert args.match == "0001"

    def test_rollback_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["rollback"])
        assert args.match is None
        assert args.revision is None
        assert args.all is False
        assert args.force is False

    def test_rollback_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["rollback", "-m", "0001", "-r", "0002", "-a", "-f"])
        assert args.match == "0001"
        assert args.revision == "0002"
        assert args.all is True
        assert args.force is True

    def test_reapply_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["reapply"])
        assert args.match is None
        assert args.revision is None
        assert args.force is False
        assert args.skip_hash_check is False

    def test_reapply_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["reapply", "-m", "0001", "-r", "0002", "-f", "--skip-hash-check"]
        )
        assert args.match == "0001"
        assert args.revision == "0002"
        assert args.force is True
        assert args.skip_hash_check is True

    def test_mark_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["mark"])
        assert args.match is None
        assert args.revision is None
        assert args.all is False

    def test_mark_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["mark", "-m", "0001", "-r", "0002", "-a"])
        assert args.match == "0001"
        assert args.revision == "0002"
        assert args.all is True

    def test_unmark_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["unmark"])
        assert args.match is None
        assert args.revision is None

    def test_unmark_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["unmark", "-m", "0001", "-r", "0002"])
        assert args.match == "0001"
        assert args.revision == "0002"

    def test_new_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["new"])
        assert args.message == ""

    def test_new_with_message(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["new", "-m", "add users table"])
        assert args.message == "add users table"

    def test_verbosity(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply", "-v", "-v", "-v"])
        assert args.verbosity == 3


class TestCmdApply:
    def test_calls_migrations_apply(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(
            match=None,
            revision=None,
            all=False,
            force=False,
            one=False,
            skip_hash_check=False,
        )
        _run_cmd(cmd_apply, args, mock_migrations)

        mock_migrations.apply.assert_called_once_with(
            match=None,
            revision=None,
            all=False,
            force=False,
            one=False,
            check_hashes=True,
        )

    def test_passes_all_params(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(
            match="0001",
            revision="0002",
            all=True,
            force=True,
            one=True,
            skip_hash_check=True,
        )
        _run_cmd(cmd_apply, args, mock_migrations)

        mock_migrations.apply.assert_called_once_with(
            match="0001",
            revision="0002",
            all=True,
            force=True,
            one=True,
            check_hashes=False,
        )


class TestCmdDevelop:
    def test_calls_migrations_develop(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(n=1, skip_hash_check=False)
        _run_cmd(cmd_develop, args, mock_migrations)

        mock_migrations.develop.assert_called_once_with(n=1, check_hashes=True)

    def test_passes_all_params(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(n=3, skip_hash_check=True)
        _run_cmd(cmd_develop, args, mock_migrations)

        mock_migrations.develop.assert_called_once_with(n=3, check_hashes=False)


class TestCmdList:
    def test_calls_migrations_list(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(match=None)
        _run_cmd(cmd_list, args, mock_migrations)

        mock_migrations.list.assert_called_once()


class TestCmdRollback:
    def test_calls_migrations_rollback(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(
            match=None, revision=None, all=False, force=False
        )
        _run_cmd(cmd_rollback, args, mock_migrations)

        mock_migrations.rollback.assert_called_once_with(
            match=None, revision=None, all=False, force=False
        )

    def test_passes_all_params(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(
            match="0001", revision="0002", all=True, force=True
        )
        _run_cmd(cmd_rollback, args, mock_migrations)

        mock_migrations.rollback.assert_called_once_with(
            match="0001", revision="0002", all=True, force=True
        )


class TestCmdReapply:
    def test_calls_migrations_reapply(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(
            match=None, revision=None, force=False, skip_hash_check=False
        )
        _run_cmd(cmd_reapply, args, mock_migrations)

        mock_migrations.reapply.assert_called_once_with(
            match=None, revision=None, force=False, check_hashes=True
        )

    def test_passes_all_params(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(
            match="0001", revision="0002", force=True, skip_hash_check=True
        )
        _run_cmd(cmd_reapply, args, mock_migrations)

        mock_migrations.reapply.assert_called_once_with(
            match="0001", revision="0002", force=True, check_hashes=False
        )


class TestCmdMark:
    def test_calls_migrations_mark(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(match=None, revision=None, all=False)
        _run_cmd(cmd_mark, args, mock_migrations)

        mock_migrations.mark.assert_called_once_with(
            match=None, revision=None, all=False
        )

    def test_passes_all_params(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(match="0001", revision="0002", all=True)
        _run_cmd(cmd_mark, args, mock_migrations)

        mock_migrations.mark.assert_called_once_with(
            match="0001", revision="0002", all=True
        )


class TestCmdUnmark:
    def test_calls_migrations_unmark(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(match=None, revision=None)
        _run_cmd(cmd_unmark, args, mock_migrations)

        mock_migrations.unmark.assert_called_once_with(
            match=None, revision=None
        )

    def test_passes_all_params(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(match="0001", revision="0002")
        _run_cmd(cmd_unmark, args, mock_migrations)

        mock_migrations.unmark.assert_called_once_with(
            match="0001", revision="0002"
        )


class TestCmdNew:
    def test_calls_migrations_new(self, mock_migrations: MagicMock) -> None:
        args = argparse.Namespace(message="add stuff")
        _run_cmd(cmd_new, args, mock_migrations)

        mock_migrations.new.assert_called_once_with(message="add stuff")


class TestMain:
    def test_main_no_command_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("classic.migrations.cli._make_migrations"),
            patch("classic.migrations.cli.Settings"),
        ):
            result = main([])
        assert result == 1

    def test_main_apply_returns_0(self) -> None:
        mock = MagicMock()
        with (
            patch("classic.migrations.cli._make_migrations", return_value=mock),
            patch("classic.migrations.cli.Settings"),
        ):
            result = main(["apply"])
        assert result == 0