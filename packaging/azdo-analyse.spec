# PyInstaller spec for the standalone `azdo-analyse` executable.
# Build with:  python packaging/build.py   (or: pyinstaller packaging/azdo-analyse.spec)

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
VERSION = re.search(r'^__version__ = "([^"]+)"$', (SRC / "azdo_analyser" / "__init__.py").read_text(encoding="utf-8"),
                    re.MULTILINE).group(1)


def windows_version_info(version):
    """Version resource shown in the .exe's Properties > Details tab."""
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)
    numbers = tuple((list(map(int, re.findall(r"\d+", version)[:4])) + [0, 0, 0, 0])[:4])
    strings = [StringStruct("CompanyName", "ivesma"),
               StringStruct("FileDescription", "Azure DevOps Project Analysis"),
               StringStruct("FileVersion", version),
               StringStruct("InternalName", "azdo-analyse"),
               StringStruct("OriginalFilename", "azdo-analyse.exe"),
               StringStruct("ProductName", "azdo-analyser"),
               StringStruct("ProductVersion", version)]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[StringFileInfo([StringTable("040904B0", strings)]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
    )


# Built-in plugins are loaded with importlib.import_module, so static analysis cannot see them.
hiddenimports = collect_submodules("azdo_analyser")
datas = collect_data_files("azdo_analyser", includes=["templates/*.j2"])

# Optional LLM summary support (--llm-summary) is bundled when the package is installed.
try:
    import anthropic  # noqa: F401
    hiddenimports += collect_submodules("anthropic")
    datas += collect_data_files("anthropic")
except ImportError:
    pass

a = Analysis(
    [str(SRC / "azdo_analyser" / "__main__.py")],
    pathex=[str(SRC)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "streamlit", "pytest", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="azdo-analyse",
    console=True,
    strip=False,
    upx=False,
    version=windows_version_info(VERSION) if sys.platform == "win32" else None,
)
