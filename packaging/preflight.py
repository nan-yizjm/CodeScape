"""Installer-contained checker: never creates databases, migrates, or requests models."""
import json
import os
import sys
from pathlib import Path

from codescape.release_check import ReleaseCheckError, validate_release_data

if __name__ == "__main__":
    try:
        resources = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        contract = json.loads((resources / "schema-support.json").read_text(encoding="utf-8"))
        root = Path(os.environ.get("CODESCAPE_DATA_DIR", str(Path(os.environ["LOCALAPPDATA"]) / "CodeScape")))
        validate_release_data(root, contract)
    except (OSError, ValueError, ReleaseCheckError):
        sys.exit(2)
