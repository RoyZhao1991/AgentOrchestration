import sys

import pytest

from src.cli import main as cli_main


def test_status_watch_interrupt_returns_documented_code(monkeypatch, capsys):
    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_main.time, "sleep", interrupt)

    exit_code = cli_main._run_status(watch=True)

    captured = capsys.readouterr()
    assert exit_code == cli_main.STATUS_WATCH_INTERRUPT_EXIT_CODE
    assert "Checking agent status..." in captured.out
    assert "Status watch interrupted; exiting cleanly." in captured.err
    assert "Traceback" not in captured.err


def test_cli_status_watch_exits_cleanly_on_interrupt(monkeypatch, capsys):
    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "argv", ["ao", "status", "--watch"])
    monkeypatch.setattr(cli_main.time, "sleep", interrupt)

    with pytest.raises(SystemExit) as exc_info:
        cli_main.cli()

    captured = capsys.readouterr()
    assert exc_info.value.code == cli_main.STATUS_WATCH_INTERRUPT_EXIT_CODE
    assert "Status watch interrupted; exiting cleanly." in captured.err
    assert "Traceback" not in captured.err
