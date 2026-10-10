import argparse
import asyncio
import json
import os
import secrets
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app
from .instance import LocalInstance, secure_directory
from .security import data_root
from .storage_schema import KNOWN_REVISIONS, SCHEMA_VERSION, StorageError, check_database


def main():
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="启动 CodeScape 本地源码学习空间")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--ai-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--index-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prepare-update", action="store_true", help="安全保存并停止本地应用，校验现有数据")
    args = parser.parse_args()
    if args.ai_worker:
        from .ai_worker import main as worker_main
        worker_main()
        return
    if args.index_worker:
        from .index_worker import main as index_main
        index_main()
        return
    if args.port != 0 and not 1024 <= args.port <= 65535:
        parser.exit(2, "端口范围为 1024 至 65535；0 表示自动选择。\n")
    root = data_root()
    instance = LocalInstance(root)
    if args.prepare_update:
        try:
            if not instance.primary:
                instance.stop_for_update()
            if (root / "app.db").exists():
                version, revision, _ = check_database(root / "app.db")
                if version > SCHEMA_VERSION or revision not in {None, *KNOWN_REVISIONS}:
                    raise StorageError("数据库来自更新版本，不能继续安装旧版本。")
            print("应用已安全保存并停止，现有数据校验通过；安装数据不会被清理。")
        except (OSError, RuntimeError):
            parser.exit(2, "应用保存或数据校验未通过，安装不能继续；请保留应用数据并排查。\n")
        finally:
            instance.close()
        return
    runtime = root / "run"
    developer_runtime = (Path.cwd() / ".runtime"
                         if not getattr(sys, "frozen", False) and "CODESCAPE_DATA_DIR" not in os.environ else None)

    def save_launch(url: str):
        value = json.dumps({"url": url, "port": int(url.split(":")[2].split("/")[0])})
        (runtime / "launch.json").write_text(value, encoding="utf-8")
        if developer_runtime:
            secure_directory(developer_runtime, instance.sid)
            (developer_runtime / "launch.json").write_text(value, encoding="utf-8")

    if not instance.primary:
        try:
            url = instance.activate()
            save_launch(url)
            if not args.no_browser:
                webbrowser.open(url)
            print("已复用正在运行的 CodeScape 实例。")
        except (OSError, RuntimeError):
            parser.exit(2, "现有实例未能响应，请稍后重试；没有启动第二个服务。\n")
        finally:
            instance.close()
        return

    bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server = None
    try:
        try:
            bound.bind(("127.0.0.1", args.port))
            bound.listen(128)
        except OSError:
            parser.exit(2, "指定端口已被占用，请重新选择端口或使用默认自动端口。\n")
        port = bound.getsockname()[1]
        nonce = secrets.token_urlsafe(32)
        url = f"http://127.0.0.1:{port}/#nonce={nonce}"

        async def idle_exit():
            if server:
                server.should_exit = True

        try:
            app = create_app(root, nonce, on_idle_exit=idle_exit)
        except StorageError as error:
            parser.exit(2, f"{error}\n")

        def issue_url():
            if not app.state.ready_event.wait(10):
                raise RuntimeError("应用尚未启动完成。")
            return f"http://127.0.0.1:{port}/#nonce={app.state.issue_nonce()}"

        def stop_for_update():
            if not app.state.ready_event.wait(10) or not server:
                return False
            asyncio.run_coroutine_threadsafe(app.state.request_shutdown(), app.state.loop)
            return True

        instance.serve(issue_url, stop_for_update)
        save_launch(url)
        if not args.no_browser:
            def open_when_ready():
                if app.state.ready_event.wait(30):
                    webbrowser.open(url)
            threading.Thread(target=open_when_ready, daemon=True).start()
        print(f"CodeScape 运行于 http://127.0.0.1:{port}，数据保存在 {root}")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                             timeout_graceful_shutdown=30))
        server.run(sockets=[bound])
    finally:
        bound.close()
        instance.close()
        (runtime / "launch.json").unlink(missing_ok=True)
        if developer_runtime:
            (developer_runtime / "launch.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
