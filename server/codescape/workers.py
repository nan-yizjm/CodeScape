from __future__ import annotations

import ctypes
from ctypes import wintypes


class IOCounts(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounts),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class WorkerJob:
    """Bound the application parser and its descendants; closing kills the whole worker tree."""

    def __init__(self, memory_limit: int = 2 * 1024**3):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise RuntimeError("无法创建解析隔离容器。")
        information = ExtendedLimits()
        information.BasicLimitInformation.LimitFlags = 0x2000 | 0x100 | 0x200
        information.ProcessMemoryLimit = memory_limit
        information.JobMemoryLimit = memory_limit
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
            self.close()
            raise RuntimeError("无法设置解析资源上限。")

    def attach(self, pid: int) -> None:
        process = self.kernel.OpenProcess(0x100 | 0x1 | 0x1000, False, pid)
        if not process:
            raise RuntimeError("无法验证解析进程。")
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise RuntimeError("无法隔离解析进程；已停止解析。")
        finally:
            self.kernel.CloseHandle(process)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
