"""Keep an owned browser process tree tied to Toolkit's lifetime."""

import ctypes
import subprocess
import time
from ctypes import wintypes

from ._core import fn, kernel32, last_error


class _Limits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
    ]


class _Counters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _Limits), ("IoInfo", _Counters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _Startup(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
    ]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE), ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]


_CreateJobObject = fn(kernel32, "CreateJobObjectW", wintypes.HANDLE, ctypes.c_void_p, wintypes.LPCWSTR)
_SetInformationJobObject = fn(kernel32, "SetInformationJobObject", wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
_QueryInformationJobObject = fn(kernel32, "QueryInformationJobObject", wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                wintypes.DWORD, ctypes.c_void_p)
_AssignProcessToJobObject = fn(kernel32, "AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
_TerminateJobObject = fn(kernel32, "TerminateJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
_OpenProcess = fn(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_CloseHandle = fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_CreateProcess = fn(kernel32, "CreateProcessW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_Startup), ctypes.POINTER(_ProcessInfo))
_ResumeThread = fn(kernel32, "ResumeThread", wintypes.DWORD, wintypes.HANDLE)
_WaitForSingleObject = fn(kernel32, "WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
_GetExitCodeProcess = fn(kernel32, "GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_TerminateProcess = fn(kernel32, "TerminateProcess", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)


class Process:
    """An owned native process; release its handle as soon as exit is observed."""

    __slots__ = ("handle", "pid", "returncode")

    def __init__(self, handle: int, pid: int) -> None:
        self.handle, self.pid = handle, pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if not self.handle or _WaitForSingleObject(self.handle, 0) == 258:  # WAIT_TIMEOUT
            return self.returncode
        return self.wait(timeout=0)

    def wait(self, timeout: float) -> int:
        if self.returncode is not None:
            return self.returncode
        result = _WaitForSingleObject(self.handle, max(0, int(timeout * 1000)))
        if result == 258:
            raise subprocess.TimeoutExpired(str(self.pid), timeout)
        if result != 0:
            raise last_error()
        code = wintypes.DWORD()
        if not _GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise last_error()
        self.returncode = int(code.value)
        _CloseHandle(self.handle)
        self.handle = 0
        return self.returncode

    def kill(self) -> None:
        if self.poll() is None and not _TerminateProcess(self.handle, 1):
            raise last_error()


class ProcessJob:
    """Closing this handle, including on a Toolkit crash, terminates owned children."""

    __slots__ = ("handle",)

    def __init__(self) -> None:
        self.handle = int(_CreateJobObject(None, None) or 0)
        if not self.handle:
            raise last_error()
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = last_error()
            self.close()
            raise error

    def add(self, pid: int) -> None:
        process = _OpenProcess(0x0101, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not process:
            raise last_error()
        try:
            if not _AssignProcessToJobObject(self.handle, process):
                raise last_error()
        finally:
            _CloseHandle(process)

    def start(self, arguments: list[str]) -> Process:
        """Assign the suspended process before it can create any browser children."""
        startup, info = _Startup(), _ProcessInfo()
        startup.cb = ctypes.sizeof(startup)
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
        flags = 0x0800_0004  # CREATE_NO_WINDOW | CREATE_SUSPENDED
        if not _CreateProcess(arguments[0], command, None, None, False, flags, None, None, ctypes.byref(startup), ctypes.byref(info)):
            raise last_error()
        process = Process(int(info.process), int(info.pid))
        try:
            if not _AssignProcessToJobObject(self.handle, info.process) or _ResumeThread(info.thread) == 0xFFFF_FFFF:
                raise last_error()
            return process
        except BaseException:
            process.kill()
            process.wait(timeout=2)
            raise
        finally:
            _CloseHandle(info.thread)

    def close(self) -> None:
        if self.handle:
            try:
                if not _TerminateJobObject(self.handle, 1):
                    raise last_error()
                deadline = time.monotonic() + 2
                while True:
                    status = _Accounting()
                    if not _QueryInformationJobObject(self.handle, 1, ctypes.byref(status), ctypes.sizeof(status), None):
                        raise last_error()
                    if not status.ActiveProcesses:
                        break
                    if time.monotonic() >= deadline:
                        raise OSError("The owned browser processes did not finish exiting in time.")
                    time.sleep(.02)
            finally:
                _CloseHandle(self.handle)
                self.handle = 0
