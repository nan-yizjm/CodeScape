import argparse
import json
import secrets
import socket
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app
from .security import data_root

parser = argparse.ArgumentParser(description="启动 CodeScape 本地开发原型")
parser.add_argument("--port", type=int, default=8123)
parser.add_argument("--no-browser", action="store_true")
args = parser.parse_args()
if not 1024 <= args.port <= 65535:
    parser.exit(2, "端口范围为 1024 至 65535。\n")
# Check before opening the database, so a second launch cannot reset an active task.
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    try:
        probe.bind(("127.0.0.1", args.port))
    except OSError:
        parser.exit(
            2, "启动失败：端口已被使用。若 CodeScape 已启动，请使用现有页面；启动新实例前先停止旧服务。\n"
        )
nonce = secrets.token_urlsafe(32)
url = f"http://127.0.0.1:{args.port}/#nonce={nonce}"
runtime = Path.cwd() / ".runtime"
runtime.mkdir(exist_ok=True)
(runtime / "launch.json").write_text(json.dumps({"url": url, "port": args.port}), encoding="utf-8")
if not args.no_browser:
    threading.Timer(2, lambda: webbrowser.open(url)).start()
print(f"CodeScape 开发原型运行于 http://127.0.0.1:{args.port}，数据保存在 {data_root()}")
uvicorn.run(create_app(nonce=nonce), host="127.0.0.1", port=args.port, log_level="warning")
