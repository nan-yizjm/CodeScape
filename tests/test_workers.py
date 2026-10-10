import subprocess
import sys
from ctypes import wintypes

from codescape.workers import WorkerJob


def test_closing_parser_job_terminates_owned_descendants():
    code = ("import subprocess,sys,time; input(); "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)']); "
            "print(child.pid,flush=True); time.sleep(90)")
    process = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    job = WorkerJob()
    child_handle = None
    try:
        job.attach(process.pid)
        process.stdin.write(b"\n")
        process.stdin.flush()
        child_pid = int(process.stdout.readline())
        child_handle = job.kernel.OpenProcess(0x100000, False, child_pid)
        assert child_handle
        assert process.poll() is None
        job.close()
        process.wait(timeout=5)
        job.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        job.kernel.WaitForSingleObject.restype = wintypes.DWORD
        assert job.kernel.WaitForSingleObject(child_handle, 5000) == 0
    finally:
        if child_handle:
            job.kernel.CloseHandle(child_handle)
        job.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_parser_memory_limit_refuses_excess_allocation():
    code = "input(); buffer=bytearray(128*1024**2); print('unexpected-allocation')"
    process = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    job = WorkerJob(memory_limit=32 * 1024**2)
    try:
        job.attach(process.pid)
        output, error = process.communicate(b"\n", timeout=5)
        assert process.returncode != 0
        assert b"unexpected-allocation" not in output
        assert b"MemoryError" in error
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
