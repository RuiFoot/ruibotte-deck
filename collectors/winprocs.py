"""Windows 전체 프로세스 목록을 한 번의 시스템 호출로 읽기 (이름 · CPU 시간 · 메모리).

psutil은 보호된 프로세스(svchost 등)마다 시스템 전체 목록을 다시 조회해서, 프로세스가 수백 개면
한 번 훑는 데 CPU 수십 초가 든다(2026-10-16 이 PC: 366개에 26초). NtQuerySystemInformation(
SystemProcessInformation)은 모든 프로세스 정보를 한 번에 돌려주므로 수 ms면 된다. 권한도 필요 없다.
"""
import ctypes
import struct
import sys

_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_SystemProcessInformation = 5

# SYSTEM_PROCESS_INFORMATION (64비트) 필드 위치
_OFF_NEXT = 0
_OFF_USER = 40
_OFF_KERNEL = 48
_OFF_NAME_LEN = 56
_OFF_NAME_PTR = 64
_OFF_PID = 80
_OFF_WS = 144           # WorkingSetSize (= psutil memory_info().rss)

_ntdll = None
_buf_size = 1 << 20


def available():
    return sys.platform == "win32" and struct.calcsize("P") == 8


def snapshot():
    """{pid: (이름, CPU 시간(초, 사용자+커널), 작업 집합 바이트)}"""
    global _ntdll, _buf_size
    if _ntdll is None:
        _ntdll = ctypes.WinDLL("ntdll")
        _ntdll.NtQuerySystemInformation.restype = ctypes.c_ulong
    while True:
        buf = ctypes.create_string_buffer(_buf_size)
        ret_len = ctypes.c_ulong(0)
        st = _ntdll.NtQuerySystemInformation(_SystemProcessInformation, buf, _buf_size, ctypes.byref(ret_len))
        if st == _STATUS_INFO_LENGTH_MISMATCH:
            _buf_size = max(_buf_size * 2, ret_len.value + 65536)
            continue
        if st != 0:
            raise OSError(f"NtQuerySystemInformation 0x{st:08x}")
        break
    raw = buf.raw
    base = ctypes.addressof(buf)
    out, off = {}, 0
    while True:
        nxt = struct.unpack_from("<I", raw, off + _OFF_NEXT)[0]
        user, kernel = struct.unpack_from("<qq", raw, off + _OFF_USER)
        name_len = struct.unpack_from("<H", raw, off + _OFF_NAME_LEN)[0]
        name_ptr = struct.unpack_from("<Q", raw, off + _OFF_NAME_PTR)[0]
        pid = struct.unpack_from("<Q", raw, off + _OFF_PID)[0]
        ws = struct.unpack_from("<Q", raw, off + _OFF_WS)[0]
        if name_ptr and name_len and base <= name_ptr < base + len(raw):
            s = name_ptr - base
            name = raw[s:s + name_len].decode("utf-16-le", "replace")
        else:
            name = "System Idle Process" if pid == 0 else "System"
        out[pid] = (name, (user + kernel) / 1e7, ws)
        if not nxt:
            break
        off += nxt
    return out


if __name__ == "__main__":
    import time
    t = time.process_time()
    s = snapshot()
    print(len(s), "procs", round((time.process_time() - t) * 1000, 1), "ms cpu")
    for pid, v in sorted(s.items(), key=lambda x: -x[1][2])[:5]:
        print(pid, v[0], round(v[1], 1), "s", v[2] // 2**20, "MB")
