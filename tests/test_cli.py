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
    for topic in ("overview", "writes", "reads", "policies", "integrate", "changelog"):
        assert topic in out


def test_docs_changelog_topic_reports_updates(capsys):
    # The bundled changelog ships what was fixed / improved / added with the package.
    assert main(["docs", "changelog"]) == 0
    out = capsys.readouterr().out
    assert "0.5.0" in out
    for term in ("New", "Fixed", "render_spoken", "reconcile_turn", "shadow"):
        assert term in out


def test_docs_unknown_topic_errors(capsys):
    assert main(["docs", "nope"]) == 1
    assert "no docs topic" in capsys.readouterr().err


def test_docs_dump_writes_all_pages(tmp_path, capsys):
    dest = tmp_path / "out"  # nested path -> also exercises mkdir(parents=True)
    assert main(["docs", "--dump", str(dest)]) == 0
    written = {p.name for p in dest.glob("*.md")}
    # Every known topic is written, and pages are non-empty.
    for topic in ("overview", "writes", "reads", "policies", "integrate", "changelog"):
        assert f"{topic}.md" in written
        assert (dest / f"{topic}.md").read_text(encoding="utf-8").strip()
    assert "Wrote" in capsys.readouterr().out


def test_docs_dump_single_topic(tmp_path, capsys):
    dest = tmp_path / "one"
    assert main(["docs", "changelog", "--dump", str(dest)]) == 0
    written = {p.name for p in dest.glob("*.md")}
    assert written == {"changelog.md"}  # only the requested page, nothing else
    assert (dest / "changelog.md").read_text(encoding="utf-8").strip()
    assert "Wrote 1 docs" in capsys.readouterr().out


def test_docs_dump_unknown_topic_errors(tmp_path, capsys):
    assert main(["docs", "nope", "--dump", str(tmp_path)]) == 1
    assert "no docs topic" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.md"))  # nothing written on a bad topic


def test_upgrade_invokes_pip(monkeypatch, capsys):
    captured = {}

    def fake_run(cmd):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr("saidso.cli._run_pip", fake_run)
    monkeypatch.setattr("saidso.cli._cleanup_pip_temp", lambda: None)
    assert main(["upgrade"]) == 0
    cmd = captured["cmd"]
    assert cmd[:3] == [sys.executable, "-m", "pip"]
    assert "--upgrade" in cmd and "saidso" in cmd


def test_uninstall_invokes_pip(monkeypatch, capsys):
    captured = {}

    def fake_run(cmd):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr("saidso.cli._run_pip", fake_run)
    monkeypatch.setattr("saidso.cli._cleanup_pip_temp", lambda: None)
    assert main(["uninstall"]) == 0
    cmd = captured["cmd"]
    assert cmd[:3] == [sys.executable, "-m", "pip"]
    assert "uninstall" in cmd and "-y" in cmd and "saidso" in cmd


def test_run_pip_filters_benign_temp_warning(monkeypatch, capsys):
    from saidso import cli

    class FakeProc:
        stdout = iter([
            "Successfully installed saidso-0.5.0\n",
            "  WARNING: Failed to remove contents in a temporary directory "
            "'C:\\Temp\\pip-uninstall-xyz'.\n",
            "  You can safely remove it manually.\n",
        ])

        def wait(self):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: FakeProc())
    rc = cli._run_pip(["x", "-m", "pip"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Successfully installed saidso-0.5.0" in out      # real output kept
    assert "Failed to remove contents" not in out            # benign warning dropped
    assert "safely remove it manually" not in out


def test_cleanup_pip_temp_removes_leftover(monkeypatch, tmp_path):
    from saidso import cli

    leftover = tmp_path / "pip-uninstall-abc123"
    leftover.mkdir()
    (leftover / "stuck.exe").write_text("x")
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path))
    cli._cleanup_pip_temp()
    assert not leftover.exists()  # stale backup dir swept
