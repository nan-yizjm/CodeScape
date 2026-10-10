from pathlib import Path

repository = Path(SPEC).resolve().parent.parent
a = Analysis([str(repository / "packaging/preflight.py")], pathex=[str(repository / "server")],
    datas=[(str(repository / ".runtime/packaging/resources/schema-support.json"), ".")],
    binaries=[], hiddenimports=[], excludes=["codescape.app", "uvicorn", "httpx", "sqlalchemy", "alembic"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="CodeScapeDataCheck", console=True,
          hide_console="hide-early", debug=False, strip=False, upx=False)
