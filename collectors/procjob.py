"""자식 프로세스(ssh 등)를 이 앱과 함께 끝나게 만드는 도우미.

Windows는 부모가 죽어도 자식이 남는다(트레이 '종료'는 os._exit, 강제 종료도 마찬가지).
남은 ssh가 원격 에이전트 연결을 계속 붙잡지 않도록, 자식을 'Job Object(닫히면 모두 종료)'에 넣는다.
다른 OS에서는 그냥 subprocess 와 같다.
"""
import subprocess
import sys

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_job = None


def _get_job():
    global _job
    if _job is not None or sys.platform != "win32":
        return _job
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps",
                                                        "ReadBytes", "WriteBytes", "OtherBytes")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # Extended limit info
            return None
        _job = (k32, job)
    except Exception:
        _job = None
    return _job


def _attach(p):
    j = _get_job()
    if j:
        try:
            j[0].AssignProcessToJobObject(j[1], int(p._handle))
        except Exception:
            pass
    return p


def popen(args, **kw):
    kw.setdefault("creationflags", NO_WINDOW)
    return _attach(subprocess.Popen(args, **kw))


def run(args, input=None, timeout=None):
    """subprocess.run(capture_output=True) 와 같지만 Job에 넣고, 시간 초과 시 자식을 확실히 죽인다."""
    p = popen(args, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise
    return subprocess.CompletedProcess(args, p.returncode, out, err)
