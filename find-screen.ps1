# 대시보드를 띄울 화면 찾기 (Windows).
#   -List            : 연결된 화면 목록을 JSON으로 출력 (설정 화면용)
#   -Monitor auto    : 노트북 내장 화면, 없으면 주 모니터 (기본값)
#   -Monitor primary : 주 모니터
#   -Monitor <ID>    : 모니터 하드웨어 ID 일부(예: SDC4160, DEL40F7)가 맞는 화면
#   -Monitor \\.\DISPLAY2 : 장치 이름으로 (같은 모델 모니터가 여러 대일 때)
# 찾으면 "x y width height" 출력, 못 찾으면 출력 없이 exit 1.
param([string]$Monitor = 'auto', [switch]$List)

Add-Type @"
using System; using System.Runtime.InteropServices;
public class DD {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct DISPLAY_DEVICE {
    public int cb; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string DeviceName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string DeviceString; public int StateFlags;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string DeviceID;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string DeviceKey; }
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern bool EnumDisplayDevices(string dev, uint i, ref DISPLAY_DEVICE dd, uint flags);
}
"@
Add-Type -AssemblyName System.Windows.Forms

# 내장 패널 판별: WmiMonitorConnectionParams.VideoOutputTechnology = 0x80000000 (internal)
$internal = @{}
try {
  Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorConnectionParams -ErrorAction Stop | ForEach-Object {
    if ($_.InstanceName -match '^DISPLAY\\([^\\]+)\\') { $internal[$Matches[1]] = ([int64]$_.VideoOutputTechnology -eq 2147483648) }
  }
} catch {}

$screens = foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
  $mon = New-Object DD+DISPLAY_DEVICE; $mon.cb = [Runtime.InteropServices.Marshal]::SizeOf($mon)
  $id = ''; $name = ''
  if ([DD]::EnumDisplayDevices($s.DeviceName, 0, [ref]$mon, 0)) {
    $name = $mon.DeviceString
    if ($mon.DeviceID -match '^MONITOR\\([^\\]+)\\') { $id = $Matches[1] }
  }
  $b = $s.Bounds
  [pscustomobject]@{ id = $id; name = $name; device = $s.DeviceName; primary = $s.Primary;
                     internal = [bool]$internal[$id]; x = $b.X; y = $b.Y; width = $b.Width; height = $b.Height }
}

if ($List) { ConvertTo-Json @($screens) -Compress; exit 0 }

$pick = $null
if ($Monitor -eq 'auto') {
  $pick = @($screens | Where-Object internal)[0]
  if (-not $pick) { $pick = @($screens | Where-Object primary)[0] }
} elseif ($Monitor -eq 'primary') {
  $pick = @($screens | Where-Object primary)[0]
} elseif ($Monitor.StartsWith('\\.\')) {
  $pick = @($screens | Where-Object { $_.device -eq $Monitor })[0]   # 같은 모델 모니터가 여러 대일 때
} else {
  $pick = @($screens | Where-Object { $_.id -and $_.id -like "*$Monitor*" })[0]
}
if (-not $pick) { exit 1 }
"$($pick.x) $($pick.y) $($pick.width) $($pick.height)"
exit 0
