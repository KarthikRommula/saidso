"""The ``saidso`` command-line interface (stdlib only).

saidso is a library — it runs inside your agent — so the CLI is intentionally
small: report the version, and scaffold a runnable quickstart you can explore.

    saidso --version          # or: saidso version
    saidso quickstart [DIR]    # write a runnable example + getting-started doc
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import List, Optional

from . import __version__

_TEMPLATES = "_templates"


def _write_templates(dest: Path) -> List[str]:
    """Copy every bundled template into ``dest``; return the filenames written."""
    written: List[str] = []
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


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"saidso {__version__}")
    return 0


def _cmd_upgrade(_args: argparse.Namespace) -> int:
    """Upgrade the installed saidso to the latest release on PyPI, via pip."""
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "saidso"]
    print("$ " + " ".join(cmd))
    try:
        return subprocess.call(cmd)
    except OSError as exc:  # pip not available in this environment
        print(
            f"saidso: could not run pip ({exc}). Upgrade manually with "
            "`pip install --upgrade saidso`.",
            file=sys.stderr,
        )
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="saidso",
        description="A grounding firewall for action-taking AI agents.",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"saidso {__version__}"
    )
    sub = parser.add_subparsers(dest="command", title="commands", metavar="<command>")

    sub.add_parser("version", help="print the installed saidso version") \
        .set_defaults(func=_cmd_version)

    sub.add_parser("upgrade", help="upgrade saidso to the latest release on PyPI (via pip)") \
        .set_defaults(func=_cmd_upgrade)

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


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
