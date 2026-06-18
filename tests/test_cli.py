import pathlib

from orb.cli import main

FIXTURE = str(pathlib.Path(__file__).parent / "fixtures" / "asian_session_long.csv")


def _flags():
    return ["--range-min", "3", "--atr-period", "3", "--roc-period", "2",
            "--roc-min", "0", "--quiet"]


def test_cli_replay_emits_signals(capsys):
    rc = main(["replay", FIXTURE, *_flags()])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ENTRY" in out and "EXIT" in out
    assert "reason=breakout_long" in out
    assert "reason=range_reentry" in out


def test_cli_replay_json(capsys):
    rc = main(["replay", FIXTURE, *_flags(), "--json"])
    assert rc == 0
    import json
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    kinds = [d["kind"] for d in lines]
    assert kinds == ["ENTRY", "EXIT"]


def test_cli_summary_on_stderr(capsys):
    main(["replay", FIXTURE, *_flags()])
    err = capsys.readouterr().err
    assert "SUMMARY" in err and "entries=1" in err


def test_cli_session_open_auto_uses_first_candle(capsys):
    rc = main(["replay", FIXTURE, *_flags(), "--session-open", "auto"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ENTRY" in out  # fixture starts 00:00, auto == default behavior


def test_cli_warns_when_session_open_outside_data(capsys):
    rc = main(["replay", FIXTURE, *_flags(), "--session-open", "23:00"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "no signals" in err and "--session-open auto" in err
