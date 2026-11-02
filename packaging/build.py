"""Build a standalone `azdo-analyse` executable for the current OS and package it for release.

PyInstaller cannot cross-compile: run this on Windows, Linux and macOS (the GitHub Actions
workflow in .github/workflows/build.yml does this for every platform).

    python packaging/build.py                    # version from the branch name, build + smoke test + archive
    python packaging/build.py --version 1.2.3    # explicit version
    python packaging/build.py --no-test          # skip the --demo smoke test
    python packaging/build.py --branch v1.2.3 --print-version   # just resolve the version

The version is taken from a branch named like `v1.2.3`, `1.2.3` or `release/v1.2.3` and stamped into
src/azdo_analyser/__init__.py before building, so `azdo-analyse --version`, the reports and the Windows
file properties all carry it. Other branch names (e.g. main) keep the version already in __init__.py.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "azdo-analyse.spec"
DIST = ROOT / "dist"
INIT = ROOT / "src" / "azdo_analyser" / "__init__.py"

VERSION_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?)$")
INIT_VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)


def platform_tag() -> str:
    os_name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    arch = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(arch, arch)
    return f"{os_name}-{arch}"


def version() -> str:
    return INIT_VERSION_RE.search(INIT.read_text(encoding="utf-8")).group(1)


def current_branch() -> str:
    """Branch being built: PR source branch first, then the CI branch, then the local git branch."""
    pr_source = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("SYSTEM_PULLREQUEST_SOURCEBRANCH")
    if pr_source:
        return pr_source
    for var in ("GITHUB_REF_NAME", "BUILD_SOURCEBRANCH"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def version_from_branch(branch: str) -> str | None:
    name = branch.removeprefix("refs/heads/").rsplit("/", 1)[-1]
    match = VERSION_RE.match(name)
    return match.group(1) if match else None


def stamp_version(new: str) -> None:
    text = INIT.read_text(encoding="utf-8")
    if INIT_VERSION_RE.search(text).group(1) != new:
        INIT.write_text(INIT_VERSION_RE.sub(f'__version__ = "{new}"', text), encoding="utf-8")
        print(f"Stamped version {new} into {INIT.relative_to(ROOT)}")


def run(*cmd: str | Path) -> None:
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


def smoke_test(exe: Path) -> None:
    print(f"+ {exe} --version", flush=True)
    reported = subprocess.run([str(exe), "--version"], capture_output=True, text=True, check=True).stdout.strip()
    print(reported)
    if not reported.endswith(f" {version()}"):
        raise SystemExit(f"Smoke test failed: expected version {version()}, executable reports {reported!r}")
    with tempfile.TemporaryDirectory() as out:
        run(exe, "--demo", "--output", out)
        for expected in ("HTML/index.html", "Excel/ProjectAssessment.xlsx", "Json/full-export.json"):
            if not (Path(out) / expected).is_file():
                raise SystemExit(f"Smoke test failed: {expected} was not produced")
    print("Smoke test passed")


def archive(exe: Path) -> Path:
    stem = f"azdo-analyse-{version()}-{platform_tag()}"
    extras = [ROOT / "README.md", ROOT / "config.example.yaml"]
    if sys.platform == "win32":
        target = DIST / f"{stem}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(exe, f"{stem}/{exe.name}")
            for f in extras:
                zf.write(f, f"{stem}/{f.name}")
    else:
        target = DIST / f"{stem}.tar.gz"
        with tarfile.open(target, "w:gz") as tf:  # tar keeps the executable bit
            tf.add(exe, f"{stem}/{exe.name}")
            for f in extras:
                tf.add(f, f"{stem}/{f.name}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-test", action="store_true", help="skip the --demo smoke test")
    parser.add_argument("--version", dest="explicit_version", help="version to build (overrides the branch name)")
    parser.add_argument("--branch", help="derive the version from this branch name instead of the current one")
    parser.add_argument("--print-version", action="store_true",
                        help="print the version derived from the branch (empty if none) and exit")
    args = parser.parse_args()

    if args.explicit_version and not VERSION_RE.match(args.explicit_version):
        parser.error(f"--version {args.explicit_version!r} is not a semantic version such as 1.2.3")
    derived = (args.explicit_version.lstrip("v") if args.explicit_version
               else version_from_branch(args.branch if args.branch is not None else current_branch()))
    if args.print_version:
        print(derived or "")
        return
    if derived:
        stamp_version(derived)
    print(f"Building azdo-analyse {version()} for {platform_tag()}", flush=True)

    shutil.rmtree(ROOT / "build" / "azdo-analyse", ignore_errors=True)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", ROOT / "build", SPEC)

    exe = DIST / ("azdo-analyse.exe" if sys.platform == "win32" else "azdo-analyse")
    if not args.no_test:
        smoke_test(exe)
    print(f"Executable: {exe}\nArchive:    {archive(exe)}")


if __name__ == "__main__":
    main()
