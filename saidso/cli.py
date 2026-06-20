"""The ``saidso`` command-line interface (stdlib only).

saidso is a library — it runs inside your agent — so the CLI is intentionally
small: report the version, and scaffold a runnable quickstart you can explore.

    saidso --version          # or: saidso version
    saidso quickstart [DIR]    # write a runnable example + getting-started doc
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 - only used for the fixed `pip install --upgrade` argv
import sys
from importlib import resources
from pathlib import Path

from . import __version__
from .observe import _enable_windows_ansi, _supports_color

_TEMPLATES = "_templates"
_DOCS = "_docs"
_DEFAULT_TOPIC = "overview"


def _write_templates(dest: Path) -> list[str]:
    """Copy every bundled template into ``dest``; return the filenames written."""
    written: list[str] = []
    pkg_files = resources.files(__package__) / _TEMPLATES
    for entry in sorted(pkg_files.iterdir(), key=lambda e: e.name):
        if entry.name.startswith("_") or not entry.is_file():
            continue
        (dest / entry.name).write_bytes(entry.read_bytes())
        written.append(entry.name)
    return written


def _cmd_quickstart(args: argparse.Namespace) -> int:
    dest = Path(args.dir)
    dest.mkdir(parents=True, exist_ok=True)
    names = _write_templates(dest)
    if not names:
        print("saidso: no templates found to scaffold", file=sys.stderr)
        return 1
    print(f"Created {dest}/ with: {', '.join(names)}")
    runnable = next((n for n in names if n.endswith(".py")), None)
    if runnable:
        print(f"Try it:  python {dest / runnable}")
    return 0


def _docs_dir():
    return resources.files(__package__) / _DOCS


def _doc_topics() -> list[str]:
    return sorted(
        e.name[:-3] for e in _docs_dir().iterdir() if e.name.endswith(".md")
    )


def _render_markdown(text: str, color: bool) -> str:
    """Light terminal styling: bold/cyan headings. Body is left as readable text."""
    if not color:
        return text
    bold, cyan, reset = "\033[1m", "\033[36m", "\033[0m"
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("# "):
            out.append(f"{bold}{cyan}{line[2:]}{reset}")
        elif line.startswith("## "):
            out.append(f"{bold}{line[3:]}{reset}")
        elif line.startswith("### "):
            out.append(f"{bold}{line[4:]}{reset}")
        else:
            out.append(line)
    return "\n".join(out)


def _cmd_docs(args: argparse.Namespace) -> int:
    topics = _doc_topics()
    if args.list:
        print("saidso documentation topics:")
        for t in topics:
            print(f"  saidso docs {t}")
        return 0
    if args.dump is not None:
        dest = Path(args.dump)
        dest.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for t in topics:
            name = f"{t}.md"
            (dest / name).write_text(
                (_docs_dir() / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
            written.append(name)
        print(f"Wrote {len(written)} docs to {dest}/: {', '.join(written)}")
        return 0
    topic = args.topic or _DEFAULT_TOPIC
    # Resolve against the known topic list only — never build a path from raw
    # user input (guards against `saidso docs ../../something` traversal).
    if topic not in topics:
        print(f"saidso: no docs topic {topic!r}", file=sys.stderr)
        print("available: " + ", ".join(topics), file=sys.stderr)
        return 1
    page = _docs_dir() / f"{topic}.md"
    color = _supports_color(sys.stdout)
    if color:
        _enable_windows_ansi()
    print(_render_markdown(page.read_text(encoding="utf-8"), color))
    if not args.topic:  # showed the default page -> point at the rest
        others = ", ".join(t for t in topics if t != _DEFAULT_TOPIC)
        print(f"\nmore: {others}\nread one with:  saidso docs <topic>")
    return 0


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"saidso {__version__}")
    return 0


def _cmd_upgrade(_args: argparse.Namespace) -> int:
    """Upgrade the installed saidso to the latest release on PyPI, via pip."""
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "saidso"]
    print("$ " + " ".join(cmd))
    try:
        # Fixed argv (no shell, no user input) — safe by construction.
        return subprocess.call(cmd)  # nosec B603
    except OSError as exc:  # pip not available in this environment
        print(
            f"saidso: could not run pip ({exc}). Upgrade manually with "
            "`pip install --upgrade saidso`.",
            file=sys.stderr,
        )
        return 1


def _cmd_uninstall(_args: argparse.Namespace) -> int:
    """Uninstall the installed saidso package, via pip."""
    cmd = [sys.executable, "-m", "pip", "uninstall", "-y", "saidso"]
    print("$ " + " ".join(cmd))
    try:
        # Fixed argv (no shell, no user input) — safe by construction.
        return subprocess.call(cmd)  # nosec B603
    except OSError as exc:  # pip not available in this environment
        print(
            f"saidso: could not run pip ({exc}). Uninstall manually with "
            "`pip uninstall saidso`.",
            file=sys.stderr,
        )
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="saidso",
        description="A grounding firewall for action-taking AI agents.",
    )
    sub = parser.add_subparsers(dest="command", title="commands", metavar="<command>")

    sub.add_parser("version", help="print the installed saidso version") \
        .set_defaults(func=_cmd_version)

    sub.add_parser("upgrade", help="upgrade saidso to the latest release on PyPI (via pip)") \
        .set_defaults(func=_cmd_upgrade)

    sub.add_parser("uninstall", help="uninstall the saidso package (via pip)") \
        .set_defaults(func=_cmd_uninstall)

    dc = sub.add_parser("docs", help="show saidso documentation in the terminal")
    dc.add_argument("topic", nargs="?", help="topic to show (default: overview)")
    dc.add_argument("--list", action="store_true", help="list all topics")
    dc.add_argument(
        "--dump", nargs="?", const="saidso-docs", default=None, metavar="DIR",
        help="write all doc pages into DIR (created if missing; default: saidso-docs)",
    )
    dc.set_defaults(func=_cmd_docs)

    qs = sub.add_parser(
        "quickstart",
        help="scaffold a runnable example + getting-started doc into a folder",
    )
    qs.add_argument(
        "dir", nargs="?", default="saidso-quickstart",
        help="target folder (created if missing; default: saidso-quickstart)",
    )
    qs.set_defaults(func=_cmd_quickstart)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
