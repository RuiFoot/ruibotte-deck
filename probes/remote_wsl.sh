# WSL(Ubuntu)에서 stdin으로 실행됨: ssh host "wsl -d Ubuntu bash -s" < remote_wsl.sh
# 결과를 섹션 구분자와 함께 출력하면 remote.py가 파싱함.
echo "@@PS"
docker ps -a --format '{{json .}}' 2>&1
echo "@@STATS"
docker stats --no-stream --format '{{json .}}' 2>&1
echo "@@GPU"
nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader,nounits 2>&1
echo "@@MEM"
free -b | awk '/^Mem:/{print $2, $3, $7}'
echo "@@DF"
df -B1 --output=target,size,used / 2>/dev/null | tail -n +2
echo "@@LOAD"
cat /proc/loadavg
nproc
echo "@@UPTIME"
cut -d' ' -f1 /proc/uptime
echo "@@END"
