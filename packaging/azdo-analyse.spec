# PyInstaller spec for the standalone `azdo-analyse` executable.
# Build with:  python packaging/build.py   (or: pyinstaller packaging/azdo-analyse.spec)

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

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
)
