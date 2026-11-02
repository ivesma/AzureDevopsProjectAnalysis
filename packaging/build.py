"""Build a standalone `azdo-analyse` executable for the current OS and package it for release.

PyInstaller cannot cross-compile: run this on Windows, Linux and macOS (the GitHub Actions
workflow in .github/workflows/build.yml does this for every platform).

    python packaging/build.py            # build + smoke test + archive
    python packaging/build.py --no-test  # skip the --demo smoke test
"""

from __future__ import annotations

import argparse
import platform
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


def platform_tag() -> str:
    os_name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    arch = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(arch, arch)
    return f"{os_name}-{arch}"


def version() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from azdo_analyser import __version__
    return __version__


def run(*cmd: str | Path) -> None:
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


def smoke_test(exe: Path) -> None:
    run(exe, "--version")
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
    args = parser.parse_args()

    shutil.rmtree(ROOT / "build" / "azdo-analyse", ignore_errors=True)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", ROOT / "build", SPEC)

    exe = DIST / ("azdo-analyse.exe" if sys.platform == "win32" else "azdo-analyse")
    if not args.no_test:
        smoke_test(exe)
    print(f"Executable: {exe}\nArchive:    {archive(exe)}")


if __name__ == "__main__":
    main()
