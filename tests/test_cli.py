"""Tests for the saidso CLI (version + quickstart scaffolding)."""

from __future__ import annotations

import sys

import pytest

import saidso
from saidso.cli import main


def test_version_subcommand(capsys):
    assert main(["version"]) == 0
    assert saidso.__version__ in capsys.readouterr().out


def test_version_flag_is_not_supported(capsys):
    # Only the `version` subcommand exists; the --version flag was removed.
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code != 0


def test_quickstart_scaffolds_runnable_demo(tmp_path, capsys):
    dest = tmp_path / "qs"
    assert main(["quickstart", str(dest)]) == 0
    names = {p.name for p in dest.iterdir()}
    assert "quickstart.py" in names
    assert "GETTING_STARTED.md" in names
    # the scaffolded demo is real, importable Python
    compile((dest / "quickstart.py").read_text(encoding="utf-8"), "quickstart.py", "exec")


def test_no_args_prints_help_and_succeeds(capsys):
    assert main([]) == 0
    assert "quickstart" in capsys.readouterr().out


def test_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for command in ("version", "upgrade", "uninstall", "quickstart"):
        assert command in out


def test_docs_default_topic(capsys):
    assert main(["docs"]) == 0
    out = capsys.readouterr().out
    assert "overview" in out.lower()
    assert "more:" in out  # footer points at other topics


def test_docs_specific_topic(capsys):
    assert main(["docs", "writes"]) == 0
    assert "@grounded" in capsys.readouterr().out


def test_docs_list(capsys):
    assert main(["docs", "--list"]) == 0
    out = capsys.readouterr().out
    for topic in ("overview", "writes", "reads", "policies", "integrate"):
        assert topic in out


def test_docs_unknown_topic_errors(capsys):
    assert main(["docs", "nope"]) == 1
    assert "no docs topic" in capsys.readouterr().err


def test_docs_dump_writes_all_pages(tmp_path, capsys):
    dest = tmp_path / "out"  # nested path -> also exercises mkdir(parents=True)
    assert main(["docs", "--dump", str(dest)]) == 0
    written = {p.name for p in dest.glob("*.md")}
    # Every known topic is written, and pages are non-empty.
    for topic in ("overview", "writes", "reads", "policies", "integrate"):
        assert f"{topic}.md" in written
        assert (dest / f"{topic}.md").read_text(encoding="utf-8").strip()
    assert "Wrote" in capsys.readouterr().out


def test_upgrade_invokes_pip(monkeypatch, capsys):
    captured = {}

    def fake_call(cmd):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr("saidso.cli.subprocess.call", fake_call)
    assert main(["upgrade"]) == 0
    cmd = captured["cmd"]
    assert cmd[:3] == [sys.executable, "-m", "pip"]
    assert "--upgrade" in cmd and "saidso" in cmd


def test_uninstall_invokes_pip(monkeypatch, capsys):
    captured = {}

    def fake_call(cmd):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr("saidso.cli.subprocess.call", fake_call)
    assert main(["uninstall"]) == 0
    cmd = captured["cmd"]
    assert cmd[:3] == [sys.executable, "-m", "pip"]
    assert "uninstall" in cmd and "-y" in cmd and "saidso" in cmd
