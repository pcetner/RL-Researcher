import contextlib
import os
import signal
import subprocess
from pathlib import Path


class LockBusy(BlockingIOError):
    pass


def linux_process_identity(pid):
    """Start ticks distinguish a retained execution PID from a later reused PID."""
    try:
        fields = Path(f'/proc/{int(pid)}/stat').read_text().split(') ')[1].split()
        return {'state':fields[0], 'start_ticks':fields[19]}
    except FileNotFoundError:
        return None


@contextlib.contextmanager
def exclusive(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        f.seek(0)
        f.write(b"0")
        f.flush()
        f.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise LockBusy(str(error)) from error
        else:
            import fcntl

            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LockBusy(str(error)) from error
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def spawn(args, **kwargs):
    options = (
        {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    return subprocess.Popen(args, **options, **kwargs)


class OwnedProcess:
    """An OS job/process group captures descendants; never kill by executable name."""

    def __init__(self, proc):
        self.proc, self.job = proc, None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.job = self.kernel.CreateJobObjectW(None, None)

            class Basic(ctypes.Structure):
                _fields_ = [
                    ("process_time", ctypes.c_int64),
                    ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD),
                    ("min_ws", ctypes.c_size_t),
                    ("max_ws", ctypes.c_size_t),
                    ("active", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD),
                ]

            class Extended(ctypes.Structure):
                _fields_ = [
                    ("basic", Basic),
                    ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t),
                    ("peak_job", ctypes.c_size_t),
                ]

            info = Extended()
            info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            self.kernel.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            if not self.kernel.SetInformationJobObject(
                self.job, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                proc.kill()
                raise OSError(
                    ctypes.get_last_error(), "Cannot establish crash-safe process ownership"
                )
            if not self.job or not self.kernel.AssignProcessToJobObject(
                self.job, int(proc._handle)
            ):
                proc.kill()
                raise OSError(ctypes.get_last_error(), "Cannot establish worker process ownership")

    def kill(self):
        if self.job:
            # Some packaged Windows interpreters allow native descendants to break
            # away from inherited jobs. Capture only this still-owned process tree.
            import ctypes
            from ctypes import wintypes

            class Entry(ctypes.Structure):
                _fields_ = [
                    ("size", wintypes.DWORD),
                    ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD),
                    ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD),
                    ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD),
                    ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD),
                    ("exe", wintypes.WCHAR * 260),
                ]

            k = self.kernel
            k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
            k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
            k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k.OpenProcess.restype = wintypes.HANDLE
            k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            snapshot = k.CreateToolhelp32Snapshot(2, 0)
            entries = []
            entry = Entry()
            entry.size = ctypes.sizeof(entry)
            more = k.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                entries.append((entry.pid, entry.parent))
                more = k.Process32NextW(snapshot, ctypes.byref(entry))
            k.CloseHandle(snapshot)
            owned = {self.proc.pid}
            for _ in entries:
                prior = len(owned)
                owned.update(pid for pid, parent in entries if parent in owned)
                if len(owned) == prior:
                    break
            handles = [k.OpenProcess(1, False, pid) for pid in owned if pid != self.proc.pid]
            k.TerminateJobObject(self.job, 1)
            for handle in handles:
                if handle:
                    k.TerminateProcess(handle, 1)
                    k.CloseHandle(handle)
        elif self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)

    def close(self):
        self.kill()  # also reap any descendants left after the worker exits
        if self.job:
            self.kernel.CloseHandle(self.job)
