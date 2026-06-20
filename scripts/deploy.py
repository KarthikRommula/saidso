#!/usr/bin/env python3
"""Deploy saidso to PyPI: validate, build, check, and upload.

The PyPI token is read from a gitignored env file (default ``.env.publish``) at
runtime — it is NEVER hardcoded in this script. The file may contain either
``KEY=VALUE`` lines (e.g. ``TWINE_PASSWORD=pypi-...``) or a single bare
``pypi-...`` token; in the latter case ``TWINE_USERNAME=__token__`` is assumed.

Usage::

    python scripts/deploy.py                # gates + build + check + upload to PyPI
    python scripts/deploy.py --test         # upload to TestPyPI instead
    python scripts/deploy.py --check-only   # build + twine check, no upload
    python scripts/deploy.py --skip-gates   # don't run ruff/mypy/pytest first
    python scripts/deploy.py --tag          # also create git tag vX.Y.Z
    python scripts/deploy.py --env-file path/to/.env.publish

Publishing is irreversible: a given version can be uploaded to PyPI only once.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    """Run a command, streaming output; abort the deploy on failure."""
    printable = " ".join(cmd)
    # Never echo secrets: redact anything that looks like a token.
    printable = re.sub(r"pypi-[A-Za-z0-9_-]+", "pypi-***", printable)
    print(f"\n$ {printable}", flush=True)
    result = subprocess.run(cmd, cwd=ROOT, env=env)  # fixed argv, no shell
    if result.returncode != 0:
        sys.exit(f"deploy: aborted — `{printable}` exited {result.returncode}")


def read_version() -> str:
    text = (ROOT / "saidso" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        sys.exit("deploy: could not find __version__ in saidso/__init__.py")
    return m.group(1)


def ensure_clean_git() -> None:
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    )
    if out.stdout.strip():
        print("deploy: WARNING — working tree has uncommitted changes:")
        print(out.stdout)
        if input("continue anyway? [y/N] ").strip().lower() != "y":
            sys.exit("deploy: aborted (dirty working tree).")


def run_gates() -> None:
    """Run the quality/security gates before shipping."""
    py = sys.executable
    _run([py, "-m", "ruff", "check", "saidso", "tests"])
    _run([py, "-m", "mypy", "saidso"])
    _run([py, "-m", "bandit", "-r", "saidso", "-c", "pyproject.toml", "-q"])
    _run([py, "-m", "pytest", "-q"])


def build(version: str) -> None:
    for old in glob.glob(str(DIST / "*")):
        os.remove(old)
    _run([sys.executable, "-m", "build"])
    _run([sys.executable, "-m", "twine", "check", *_artifacts(version)])


def _artifacts(version: str) -> list[str]:
    paths = sorted(glob.glob(str(DIST / f"saidso-{version}*")))
    if not paths:
        sys.exit(f"deploy: no artifacts for {version} in {DIST}")
    return paths


def load_token(env_file: Path) -> dict[str, str]:
    """Load PyPI credentials from ``env_file`` into a copy of the environment."""
    if not env_file.exists():
        sys.exit(f"deploy: env file not found: {env_file}")
    env = dict(os.environ)
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip()
        elif line.startswith("pypi-"):  # bare token on its own line
            env.setdefault("TWINE_USERNAME", "__token__")
            env["TWINE_PASSWORD"] = line
    if not env.get("TWINE_PASSWORD"):
        sys.exit(f"deploy: no PyPI token found in {env_file.name}")
    env.setdefault("TWINE_USERNAME", "__token__")
    return env


def upload(version: str, env: dict[str, str], *, test: bool) -> None:
    cmd = [sys.executable, "-m", "twine", "upload"]
    if test:
        cmd += ["--repository", "testpypi"]
    cmd += _artifacts(version)
    _run(cmd, env=env)


def tag(version: str) -> None:
    _run(["git", "tag", f"v{version}"])
    print(f"deploy: created tag v{version} (push with: git push origin v{version})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build and publish saidso to PyPI.")
    ap.add_argument("--test", action="store_true", help="upload to TestPyPI")
    ap.add_argument("--check-only", action="store_true", help="build + check, no upload")
    ap.add_argument("--skip-gates", action="store_true", help="skip ruff/mypy/pytest")
    ap.add_argument("--tag", action="store_true", help="create git tag vX.Y.Z")
    ap.add_argument("--env-file", default=".env.publish", help="creds file (gitignored)")
    args = ap.parse_args(argv)

    version = read_version()
    print(f"deploy: saidso {version} -> {'TestPyPI' if args.test else 'PyPI'}")

    if not args.skip_gates:
        run_gates()
    build(version)

    if args.check_only:
        print("\ndeploy: --check-only — artifacts built and validated, not uploaded.")
        return 0

    ensure_clean_git()
    env = load_token((ROOT / args.env_file).resolve())
    upload(version, env, test=args.test)
    if args.tag:
        tag(version)

    print(f"\ndeploy: done. Verify with:  pip install --upgrade saidso  (-> {version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
