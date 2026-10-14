from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from ctypes import wintypes
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from pathlib import Path

from .security import _crypt


def current_user_sid() -> str:
    command = Path(os.environ["SystemRoot"]) / "System32/whoami.exe"
    result = subprocess.run([str(command), "/user", "/fo", "csv", "/nh"], capture_output=True,
                            check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    import re
    match = re.search(rb"S-1-[0-9-]+", result.stdout)
    if not match:
        raise RuntimeError("无法验证当前 Windows 用户。")
    return match[0].decode()


def secure_directory(path: Path, sid: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    executable = Path(os.environ["SystemRoot"]) / "System32/icacls.exe"
    result = subprocess.run([str(executable), str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("无法保护本地启动通信目录，已停止启动。")


class LocalInstance:
    """Per-user/profile mutex and authenticated Windows named-pipe activation."""

    def __init__(self, root: Path):
        self.sid = current_user_sid()
        identity = hashlib.sha256((self.sid + str(root.resolve()).casefold()).encode()).hexdigest()[:24]
        self.address = rf"\\.\pipe\CodeScape-{identity}"
        self.key_path = root / "run/instance-auth.dpapi"
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateMutexW(None, True, f"Local\\CodeScape-{identity}")
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.primary = ctypes.get_last_error() != 183
        self.listener = None
        self.stopped = threading.Event()
        if self.primary:
            secure_directory(self.key_path.parent, self.sid)
            self.authkey = secrets.token_bytes(32)
            temporary = self.key_path.with_suffix(".tmp")
            temporary.write_bytes(_crypt(self.authkey))
            temporary.replace(self.key_path)

    def activate(self) -> str:
        document = self._command("open")
        if "url" not in document:
            raise RuntimeError("现有实例正在退出，请稍后重试。")
        return document["url"]

    def stop_for_update(self, timeout: float = 60) -> None:
        if self._command("shutdown").get("accepted") is not True:
            raise RuntimeError("现有实例无法安全保存，请关闭界面后重试安装。")
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        result = self.kernel.WaitForSingleObject(self.handle, int(timeout * 1000))
        if result not in {0, 0x80}:
            raise RuntimeError("应用尚未完成保存，安装没有继续；请稍后重试。")
        # The wait acquires ownership of the named mutex. Release it before installation.
        self.kernel.ReleaseMutex(self.handle)

    def _command(self, command: str) -> dict:
        for _ in range(50):
            try:
                authkey = _crypt(self.key_path.read_bytes(), True)
                with Client(self.address, family="AF_PIPE", authkey=authkey) as connection:
                    connection.send_bytes(json.dumps({"command": command}).encode())
                    if not connection.poll(5):
                        raise RuntimeError("现有实例未响应，请稍后重试。")
                    return json.loads(connection.recv_bytes(4096))
            except (OSError, EOFError, AuthenticationError):
                time.sleep(0.1)
        raise RuntimeError("已有 CodeScape 实例启动中或未响应，请稍后重试。")

    def serve(self, issue_url, request_shutdown=None) -> None:
        self.listener = Listener(self.address, family="AF_PIPE", authkey=self.authkey)

        def loop():
            while not self.stopped.is_set():
                try:
                    with self.listener.accept() as connection:
                        if not connection.poll(5):
                            continue
                        request = json.loads(connection.recv_bytes(1024))
                        if request == {"command": "shutdown"}:
                            accepted = bool(request_shutdown and request_shutdown())
                            connection.send_bytes(json.dumps({"accepted": accepted}).encode())
                            continue
                        if request != {"command": "open"}:
                            continue
                        try:
                            document = {"url": issue_url()}
                        except RuntimeError:
                            document = {"error": "应用正在退出或尚未就绪。"}
                        connection.send_bytes(json.dumps(document).encode())
                except (OSError, EOFError, ValueError, AuthenticationError):
                    if self.stopped.is_set():
                        return

        threading.Thread(target=loop, name="codescape-activation", daemon=True).start()

    def close(self) -> None:
        self.stopped.set()
        if self.listener:
            self.listener.close()
        if self.handle:
            if self.primary:
                self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
