from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("此原型需要 Windows DPAPI 保存密钥。")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


class CredentialStore:
    def __init__(self, root: Path):
        self.path = root / "credentials" / "provider.dpapi"

    def save(self, key: str) -> None:
        if not key.strip():
            raise ValueError("API Key 不能为空。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(_crypt(key.strip().encode()))
        temporary.replace(self.path)

    def read(self) -> str | None:
        return _crypt(self.path.read_bytes(), True).decode() if self.path.exists() else None

    def exists(self) -> bool:
        return self.path.exists()


def data_root() -> Path:
    return Path(os.environ.get("CODESCAPE_DATA_DIR", Path(os.environ["LOCALAPPDATA"]) / "CodeScape"))
