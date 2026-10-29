$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
# Windows 공통 프로브: 이 PC와 원격 PC 모두에서 실행됨. JSON 한 줄 출력.
$os = Get-CimInstance Win32_OperatingSystem
$procs = Get-CimInstance Win32_Processor
$nlog = [int]($procs | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
if ($nlog -lt 1) { $nlog = 1 }

$cpuPerf = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor
$cpuTotal = ($cpuPerf | Where-Object Name -eq '_Total').PercentProcessorTime
$cpuPer = @($cpuPerf | Where-Object { $_.Name -ne '_Total' } | Sort-Object { [int]($_.Name -replace '\D','') } | ForEach-Object { [int]$_.PercentProcessorTime })

# 프로세스: 같은 이름(chrome#3 등)은 합산
$pp = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | Where-Object { $_.Name -notin '_Total','Idle' }
$groups = $pp | Group-Object { $_.Name -replace '#\d+$','' }
$agg = $groups | ForEach-Object {
  [pscustomobject]@{
    name = $_.Name
    cpu  = [math]::Round((($_.Group | Measure-Object PercentProcessorTime -Sum).Sum) / $nlog, 1)
    mem  = [int64](($_.Group | Measure-Object WorkingSetPrivate -Sum).Sum)
  }
}
$topCpu = @($agg | Sort-Object cpu -Descending | Select-Object -First 8 | ForEach-Object { @{ name = $_.name; cpu = $_.cpu } })
$topMem = @($agg | Sort-Object mem -Descending | Select-Object -First 8 | ForEach-Object { @{ name = $_.name; mem = $_.mem } })

$net = Get-CimInstance Win32_PerfFormattedData_Tcpip_NetworkInterface
$down = [double]($net | Measure-Object BytesReceivedPersec -Sum).Sum
$up = [double]($net | Measure-Object BytesSentPersec -Sum).Sum

$pf = Get-CimInstance Win32_PageFileUsage
# Raw counters hold cumulative bytes since boot
$netRaw = Get-CimInstance Win32_PerfRawData_Tcpip_NetworkInterface
$disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
  @{ name = $_.DeviceID; size = [int64]$_.Size; free = [int64]$_.FreeSpace }
})

# GPU: 어댑터 이름 + 3D 엔진 사용률 합계 + 전용 메모리 사용량 (NVIDIA 상세값은 nvidia-smi가 있으면 그쪽을 씀)
$vc = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notmatch 'Basic|Remote|Virtual|Mirage|Parsec|Jump' })
$eng = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine | Where-Object { $_.Name -like '*engtype_3D*' }
$gutil = [math]::Min(100, [double]($eng | Measure-Object UtilizationPercentage -Sum).Sum)
$gmem = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory
$gpus = @($vc | ForEach-Object { @{ name = $_.Name.Trim() } })

@{
  host = $env:COMPUTERNAME
  cpu = [double]$cpuTotal
  cpu_per = $cpuPer
  mem_total = [int64]$os.TotalVisibleMemorySize * 1024
  mem_free = [int64]$os.FreePhysicalMemory * 1024
  uptime_sec = [int64]((Get-Date) - $os.LastBootUpTime).TotalSeconds
  disks = $disks
  top_cpu = $topCpu
  top_mem = $topMem
  net_down = $down
  net_up = $up
  gpus = $gpus
  gpu_util = $gutil
  gpu_dedicated_used = [int64]($gmem | Measure-Object DedicatedUsage -Sum).Sum
  gpu_shared_used = [int64]($gmem | Measure-Object SharedUsage -Sum).Sum
  cpu_name = ($procs | Select-Object -First 1).Name.Trim()
  cores = [int]($procs | Measure-Object -Property NumberOfCores -Sum).Sum
  threads = $nlog
  os_name = $os.Caption
  docker_desktop = [bool](Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue)
  proc_count = @(Get-Process).Count
  pagefile_used = [int64](($pf | Measure-Object CurrentUsage -Sum).Sum) * 1MB
  pagefile_total = [int64](($pf | Measure-Object AllocatedBaseSize -Sum).Sum) * 1MB
  net_total_down = [int64]($netRaw | Measure-Object BytesReceivedPersec -Sum).Sum
  net_total_up = [int64]($netRaw | Measure-Object BytesSentPersec -Sum).Sum
} | ConvertTo-Json -Depth 4 -Compress
