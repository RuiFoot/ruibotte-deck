"""이 PC의 GPU(Windows 성능 카운터)와 Docker 상태. 수 초 걸려서 별도 주기로 수집."""
import json
import shutil
import subprocess

from collectors.remote import parse_wsl

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

GPU_PS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$vc=@(Get-CimInstance Win32_VideoController|?{$_.Name -notmatch 'Basic|Remote|Virtual|Mirage|Parsec|Jump'});"
    "$e=Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine|?{$_.Name -like '*engtype_3D*'};"
    "$m=Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory;"
    "@{names=@($vc|%{$_.Name.Trim()});util=[math]::Min(100,[double]($e|Measure-Object UtilizationPercentage -Sum).Sum);"
    "ded=[int64]($m|Measure-Object DedicatedUsage -Sum).Sum;shared=[int64]($m|Measure-Object SharedUsage -Sum).Sum}"
    "|ConvertTo-Json -Compress"
)


def _run(args, timeout):
    r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def windows_gpu():
    """Windows 카운터 기반 GPU 정보 (모든 어댑터 합산 3D 사용률)."""
    try:
        _, out, _ = _run(["powershell", "-NoProfile", "-Command", GPU_PS], 20)
        d = json.loads(out[out.find("{"):])
    except Exception:
        return []
    names = d.get("names") or []
    if isinstance(names, str):
        names = [names]
    return [{"name": n, "util": d.get("util") if i == 0 else None,
             "shared_used": d.get("shared") if i == 0 else None, "source": "windows"}
            for i, n in enumerate(names)]


def docker_status():
    if not shutil.which("docker"):
        return {"containers": [], "docker_error": "Docker 미설치"}
    try:
        code, ps, err = _run(["docker", "ps", "-a", "--format", "{{json .}}"], 10)
    except subprocess.TimeoutExpired:
        return {"containers": [], "docker_error": "Docker 응답 없음"}
    if code != 0:
        msg = "Docker 엔진 꺼짐 (Docker Desktop 미실행)" if "pipe" in err or "daemon" in err else err.strip()[:160]
        return {"containers": [], "docker_error": msg}
    stats = ""
    if ps.strip():
        try:
            _, stats, _ = _run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], 15)
        except subprocess.TimeoutExpired:
            pass
    d = parse_wsl(f"@@PS\n{ps}\n@@STATS\n{stats}\n@@END\n")
    return {"containers": d["containers"], "docker_error": None}


class LocalExtras:
    def snapshot(self):
        return {"gpus": windows_gpu(), **docker_status()}


if __name__ == "__main__":
    print(json.dumps(LocalExtras().snapshot(), indent=2, ensure_ascii=False))
