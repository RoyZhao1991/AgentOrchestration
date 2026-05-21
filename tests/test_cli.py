import sys

import pytest

from src.cli.main import cli, non_negative_int


def test_non_negative_int_accepts_zero_and_positive_values():
    assert non_negative_int("0") == 0
    assert non_negative_int("25") == 25


@pytest.mark.parametrize("value", ["-1", "-5", "not-a-number"])
def test_non_negative_int_rejects_negative_and_invalid_values(value):
    with pytest.raises(Exception, match="non-negative integer"):
        non_negative_int(value)


def test_logs_tail_rejects_negative_value(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ao", "logs", "agent-1", "--tail", "-5"])

    with pytest.raises(SystemExit) as exc_info:
        cli()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "--tail" in captured.err
    assert "non-negative integer" in captured.err


@pytest.mark.parametrize("tail_value", ["0", "5"])
def test_logs_tail_accepts_zero_and_positive_values(
    monkeypatch,
    capsys,
    tail_value,
):
    monkeypatch.setattr(
        sys,
        "argv",
        ["ao", "logs", "agent-1", "--tail", tail_value],
    )

    cli()

    captured = capsys.readouterr()
    assert "Fetching logs for agent: agent-1" in captured.out
    assert captured.err == ""
