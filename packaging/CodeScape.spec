# Windows onedir keeps standard streams for contained workers; hide the launcher's console early.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

repository = Path(SPEC).resolve().parent.parent
resources = repository / ".runtime/packaging/resources"
datas = [(str(repository / "apps/web/dist"), "apps/web/dist"),
         (str(repository / "analyzers/typescript/analyze.mjs"), "analyzers/typescript"),
         (str(repository / "node_modules/typescript"), "node_modules/typescript"),
         (str(repository / "tests/fixtures/learning_project"), "tests/fixtures/learning_project"),
         (str(resources), ".")]
datas += collect_data_files("codescape", include_py_files=True, includes=["migrations/**"])
binaries = collect_dynamic_libs("clang")
a = Analysis([str(repository / "packaging/launcher.py")], pathex=[str(repository / "server")],
    binaries=binaries, datas=datas, hiddenimports=["codescape.index_worker", "codescape.ai_worker", "clang.cindex",
        "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on"],
    excludes=["pytest", "ruff", "tkinter", "unittest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CodeScape", console=True,
          hide_console="hide-early", debug=False, strip=False, upx=False,
          icon=str(repository / "packaging/codescape.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="CodeScape")
