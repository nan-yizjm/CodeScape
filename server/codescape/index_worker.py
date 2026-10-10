"""Owned parser process; reads snapshots, never imports or executes target source."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .analyzers import analyze
from .snapshots import read_snapshot


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    request = json.loads(sys.stdin.read())
    root = Path(request["root"])
    files = read_snapshot(root, request["manifest"])
    result = analyze(root, files, request["version"])
    sys.stdout.write(result.model_dump_json())


if __name__ == "__main__":
    main()
