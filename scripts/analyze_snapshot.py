import json
import sys
from pathlib import Path

from codescape.analyzers import analyze
from codescape.snapshots import read_snapshot

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
request = json.loads(sys.stdin.read())
root = Path(request["root"])
files = read_snapshot(root, request["manifest"])
result = analyze(root, files, request["version"])
sys.stdout.write(result.model_dump_json())
