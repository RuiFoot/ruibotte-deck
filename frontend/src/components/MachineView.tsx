// 두 PC를 같은 형식(Machine)으로 맞춘 뒤 같은 배치로 그림. 한쪽에만 있는 값은 "정보 없음".
import { useState } from "react";
import type { NetResp, SysHistoryResp } from "../types";
import type { ReactNode } from "react";
import type { Container, LocalExtras, LocalState, Proc, RemoteState } from "../types";
import { C, CPU_STOPS, ago, fmtB, fmtDur, fmtRate, level, scaleColor, scaleCss, shortCpu, store, usePoll } from "../lib";
import { BarRow, Card, Gauge, Head, Icon, Muted } from "./ui";
import { NetChart, SystemHistory } from "./charts";

interface Gpu { name: string; util?: number | null; temp?: number | null; note?: string; memUsed?: number; memTotal?: number; sharedUsed?: number | null }
interface WslInfo { load?: { l1: number; cpus: number } | null; mem?: { used: number; total: number } | null }

export interface Machine {
  id?: string;   // DB 기록 조회용 PC id
  icon: ReactNode; title: string; tag: string; tagCls: string; spec: string; specFull: string;
  ok: boolean; err?: string | null;
  cpu?: number | null; cpuSub?: string; cpuPer: number[];
  coreHist: number[][]; coreSlots: number; coreSpan: string;   // 잔디 히트맵: 시간 x 코어
  mem?: number | null; memSub: string; memTotal: number;
  hist: { cpu: number[]; mem: number[]; down: number[]; up: number[] }; stepSec: number;
  net: { down: number; up: number };
  topCpu: Proc[]; topMem: Proc[];
  disks: { name: string; total: number; used: number }[]; battery: string;
  gpus: Gpu[];
  docker: { containers: Container[]; error?: string | null };
  wsl: WslInfo | null;
  info: { label: string; value: string }[];   // 시스템 정보 카드 (두 PC 같은 항목, 없으면 "정보 없음")
  summary: string;
}

const NA = "정보 없음";

export function fromLocal(L: LocalState, E: LocalExtras | null): Machine {
  const s = L.spec || {};
  return {
    icon: Icon.laptop, title: L.host, tag: "가동 " + fmtDur(L.uptime), tagCls: "g",
    spec: `${shortCpu(s.cpu_name)} · ${s.cores}C/${s.threads}T · RAM ${fmtB(L.mem.total)}`, specFull: `${s.cpu_name} / ${s.os_name}`,
    ok: true,
    cpu: L.cpu, cpuSub: L.cpu_freq ? `· ${(L.cpu_freq / 1000).toFixed(1)} GHz` : "", cpuPer: L.cpu_per,
    coreHist: L.hist_cores || [], coreSlots: 90, coreSpan: "최근 3분",
    mem: L.mem.pct, memSub: `${fmtB(L.mem.used)} / ${fmtB(L.mem.total)}`, memTotal: L.mem.total,
    hist: L.hist, stepSec: 2, net: L.net,
    topCpu: L.top_cpu, topMem: L.top_mem,
    disks: L.disks,
    battery: L.battery ? `배터리 ${Math.round(L.battery.pct)}%${L.battery.plugged ? " · 충전 중" : ""}` : "배터리 정보 없음",
    gpus: (E?.gpus || []).map((g) => ({ name: g.name.replace(/\(R\)|\(TM\)/g, ""), util: g.util, sharedUsed: g.shared_used })),
    docker: { containers: E?.containers || [], error: E ? E.docker_error : "수집 중…" },
    wsl: null,
    info: [
      { label: "운영체제", value: (s.os_name || NA).replace("Microsoft ", "") },
      { label: "가동 시간", value: fmtDur(L.uptime) },
      { label: "프로세스", value: L.proc_count != null ? `${L.proc_count}개` : NA },
      { label: "CPU 클럭", value: L.cpu_freq ? `${(L.cpu_freq / 1000).toFixed(2)} GHz` : NA },
      { label: "스왑 / 페이지 파일", value: L.swap?.total ? `${fmtB(L.swap.used)} / ${fmtB(L.swap.total)}` : NA },
      { label: "부팅 후 네트워크", value: L.net_total ? `↓ ${fmtB(L.net_total.down)} · ↑ ${fmtB(L.net_total.up)}` : NA },
      { label: "배터리", value: L.battery ? `${Math.round(L.battery.pct)}%${L.battery.plugged ? " · 충전 중" : ""}` : NA },
      { label: "연결", value: "이 PC" },
    ],
    summary: `CPU ${Math.round(L.cpu)}% · 메모리 ${Math.round(L.mem.pct)}%`,
  };
}

export function fromRemote(R: RemoteState, label: string, now: number): Machine {
  const W = R.windows || {};
  // 대체 주소(외부망 등)로 연결된 경우
  const ext = R.fallback ?? (!!R.host && R.host.includes("ext"));
  const memPct = W.mem_total ? (1 - (W.mem_free || 0) / W.mem_total) * 100 : null;
  const nv: Gpu[] = (R.gpus || []).map((g) => ({
    name: g.name.replace("NVIDIA GeForce ", "GeForce "), util: g.util, temp: g.temp,
    memUsed: (g.mem_used || 0) * 1048576, memTotal: (g.mem_total || 0) * 1048576,
  }));
  const others: Gpu[] = (W.gpus || []).filter((g) => !/nvidia/i.test(g.name)).map((g) => ({
    name: g.name.replace(/\(R\)|\(TM\)/g, ""), util: nv.length ? null : W.gpu_util, note: nv.length ? "내장 그래픽" : "",
  }));
  const lastAgo = R.last_ok ? Math.round(now - R.last_ok) : null;
  return {
    icon: Icon.server, title: W.host || label,
    tag: R.ok ? `${ext ? "외부망" : "LAN"} ${R.latency_ms}ms · 가동 ${fmtDur(W.uptime_sec)}` : lastAgo != null ? `끊김 · ${ago(lastAgo)} 데이터` : "연결 안 됨",
    tagCls: R.ok ? (ext ? "a" : "g") : "r",
    spec: W.cpu_name ? `${shortCpu(W.cpu_name)} · ${W.cores}C/${W.threads}T · RAM ${fmtB(W.mem_total)}` : "사양 수집 중…",
    specFull: `${W.cpu_name || ""} / ${W.os_name || ""}`,
    ok: R.ok, err: R.ok ? null : R.error,
    cpu: W.cpu, cpuSub: "", cpuPer: W.cpu_per || [],
    coreHist: R.hist_cores || [], coreSlots: 60, coreSpan: "최근 10분",
    mem: memPct, memSub: W.mem_total ? `${fmtB(W.mem_total - (W.mem_free || 0))} / ${fmtB(W.mem_total)}` : "", memTotal: W.mem_total || 1,
    hist: { cpu: R.hist?.cpu || [], mem: R.hist?.mem || [], down: R.hist?.down || [], up: R.hist?.up || [] }, stepSec: 10,
    net: { down: W.net_down || 0, up: W.net_up || 0 },
    topCpu: W.top_cpu || [], topMem: W.top_mem || [],
    disks: [
      ...(W.disks || []).map((d) => ({ name: d.name, total: d.size, used: d.size - d.free })),
      ...(R.disk ? [{ name: "WSL /", total: R.disk.total, used: R.disk.used }] : []),
    ],
    battery: "배터리 정보 없음",
    gpus: [...nv, ...others],
    docker: { containers: R.containers || [], error: R.docker_error },
    wsl: { load: R.load, mem: R.mem },
    info: [
      { label: "운영체제", value: (W.os_name || NA).replace("Microsoft ", "") },
      { label: "가동 시간", value: W.uptime_sec != null ? fmtDur(W.uptime_sec) : NA },
      { label: "프로세스", value: W.proc_count != null ? `${W.proc_count}개` : NA },
      { label: "CPU 클럭", value: NA },
      { label: "스왑 / 페이지 파일", value: W.pagefile_total ? `${fmtB(W.pagefile_used)} / ${fmtB(W.pagefile_total)}` : NA },
      { label: "부팅 후 네트워크", value: W.net_total_down != null ? `↓ ${fmtB(W.net_total_down)} · ↑ ${fmtB(W.net_total_up)}` : NA },
      { label: "배터리", value: NA },
      { label: "연결", value: R.ok ? `${ext ? "외부망" : "LAN"} · ${R.latency_ms}ms` : "끊김" },
    ],
    summary: !R.ok ? "연결 끊김" : memPct != null ? `CPU ${Math.round(W.cpu || 0)}% · 메모리 ${Math.round(memPct)}%` : "수집 중…",
  };
}

/** 잔디 히트맵: 세로 = 코어, 가로 = 시간(오른쪽이 지금). 샘플을 묶어 가로 30칸으로 굵게 표시 */
const HEAT_COLS = 30;
// 잔디 색: 남색 → 파랑 → (공통) 주황 → 빨강, 90% 이상은 완전한 빨강
const heatColor = (p: number) => scaleColor(CPU_STOPS, p / 100);
const heatGradient = () => scaleCss(CPU_STOPS.filter(([p]) => p <= 0.9).map(([p, c]) => [p / 0.9, c] as [number, [number, number, number]]));

function CoreHeatmap({ hist, slots, cores, span }: { hist: number[][]; slots: number; cores: number; span: string }) {
  const per = Math.max(1, Math.ceil(slots / HEAT_COLS));            // 한 칸에 묶는 샘플 수
  const recent = hist.slice(-per * HEAT_COLS);
  const buckets: (number[] | null)[] = [];
  for (let c = 0; c < HEAT_COLS; c++) {
    const end = recent.length - (HEAT_COLS - 1 - c) * per;
    const chunk = recent.slice(Math.max(0, end - per), Math.max(0, end));
    if (!chunk.length) { buckets.push(null); continue; }
    buckets.push(Array.from({ length: cores }, (_, r) => chunk.reduce((a, smp) => a + (smp[r] ?? 0), 0) / chunk.length));
  }
  const cells = [];
  for (let r = 0; r < cores; r++) {
    cells.push(<span key={`l${r}`} className="hl">{r}</span>);
    for (let c = 0; c < HEAT_COLS; c++) {
      const b = buckets[c];
      cells.push(b ? <i key={`${r}-${c}`} style={{ background: heatColor(b[r]) }} title={`코어 ${r} · ${Math.round(b[r])}%`} />
                   : <i key={`${r}-${c}`} className="empty" />);
    }
  }
  return (
    <div className="heatwrap">
      <div className="heat" style={{ gridTemplateColumns: `1.4em repeat(${HEAT_COLS}, 1fr)`, gridTemplateRows: `repeat(${cores}, 1fr)` }}>{cells}</div>
      <div className="heatfoot">
        <span>{span.replace("최근 ", "")} 전</span>
        <span className="heatlegend">
          <i style={{ background: heatGradient() }} />
          <span className="ticks"><small>0</small><small>30</small><small>60</small><small>90%+</small></span>
        </span>
        <span>지금</span>
      </div>
    </div>
  );
}

const SYS_RANGES: [string, string][] = [["live", "실시간"], ["1h", "1시간"], ["24h", "24시간"], ["7d", "7일"], ["30d", "30일"]];
const stepText = (s: number) => (s >= 3600 ? `${s / 3600}시간` : s >= 60 ? `${s / 60}분` : `${s}초`) + " 평균";

/** 시스템 추이: 실시간(메모리의 최근 기록) 또는 DB 기록(1시간~30일) */
function HistoryCard({ M }: { M: Machine }) {
  const [rng, setRng] = useState<string>(() => store.get("sys-range", "live"));
  const pick = (r: string) => { setRng(r); store.set("sys-range", r); };
  const { data, error } = usePoll<SysHistoryResp>(rng !== "live" && M.id ? `/api/machines/${M.id}/history?range=${rng}` : null, 60000);
  const pts = data?.points || [];
  const liveSpan = Math.round((Math.max(M.hist.cpu.length, 1) * M.stepSec) / 60);
  const tabs = (
    <div className="minitabs" onPointerDown={(e) => e.stopPropagation()}>
      {SYS_RANGES.map(([k, l]) => <button key={k} className={rng === k ? "on" : ""} onClick={() => pick(k)}>{l}</button>)}
    </div>
  );
  const avg = (xs: (number | null)[]) => { const v = xs.filter((x): x is number => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const peak = (xs: (number | null)[]) => { const v = xs.filter((x): x is number => x != null); return v.length ? Math.max(...v) : null; };
  const right = rng === "live"
    ? <><span style={{ color: C.green }}>↓ {fmtRate(M.net.down)}</span>&nbsp;&nbsp;<span style={{ color: C.amber }}>↑ {fmtRate(M.net.up)}</span></>
    : data?.has_data ? <>CPU 평균 {Math.round(avg(pts.map((p) => p.cpu)) ?? 0)}% · 최고 {Math.round(peak(pts.map((p) => p.cpu_max)) ?? 0)}% · 메모리 평균 {Math.round(avg(pts.map((p) => p.mem)) ?? 0)}%</> : null;
  return (
    <Card title={<>추이{tabs}</>} className="m-hist" right={right}>
      {rng === "live" || !M.id ? (
        <SystemHistory {...M.hist} stepSec={M.stepSec} />
      ) : error && !data ? <div className="err">기록을 불러오지 못함: {error}</div>
        : !data ? <Muted>불러오는 중…</Muted>
        : !data.has_data ? <Muted>이 기간의 기록이 없습니다. (대시보드가 켜져 있는 동안 쌓여요)</Muted>
        : <SystemHistory cpu={pts.map((p) => p.cpu)} mem={pts.map((p) => p.mem)} down={pts.map((p) => p.down)} up={pts.map((p) => p.up)}
            stepSec={data.step} times={pts.map((p) => p.ts)} />}
      <div className="legend">
        <span><i style={{ background: C.blue }} />CPU</span><span><i style={{ background: C.violet }} />메모리</span>
        <span><i style={{ background: C.green }} />다운로드</span><span><i style={{ background: C.amber }} />업로드</span>
        <span style={{ marginLeft: "auto" }}>{rng === "live" ? `최근 ${liveSpan}분 · ${M.stepSec}초 간격` : data ? `${stepText(data.step)} · 빈 칸 = 꺼져 있던 시간` : ""}</span>
      </div>
    </Card>
  );
}

type DetailTab = "proc" | "sys" | "disk" | "docker" | "net";

const NET_RANGES: [string, string][] = [["1h", "1시간"], ["24h", "24시간"], ["7d", "7일"], ["30d", "30일"]];
const ms = (v?: number | null) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(1)}초` : `${Math.round(v)}ms`);
const when = (t: number) => new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

/** 원격 PC 연결 품질: SSH 연결 안 왕복 시간 추이 + 끊긴 구간 */
function NetPanel({ id, rng }: { id: string; rng: string }) {
  const { data, error } = usePoll<NetResp>(`/api/machines/${id}/net?range=${rng}`, 30000);
  if (error && !data) return <div className="err">기록을 불러오지 못함: {error}</div>;
  if (!data) return <Muted>불러오는 중…</Muted>;
  if (!data.has_data) return <Muted>아직 기록이 없어요. 대시보드가 켜져 있는 동안 10초마다 재요.</Muted>;
  const S = data.summary, last = data.outages[0];
  return (
    <div className="netp">
      <div className="net-sum num">
        <span>지금 <b style={{ color: data.now.ok ? C.cyan : C.red }}>{data.now.ok ? ms(data.now.rtt) : "끊김"}</b></span>
        <span>평균 <b>{ms(S.rtt)}</b></span>
        <span>최고 <b>{ms(S.rtt_max)}</b></span>
        <span>연결 <b style={{ color: (S.uptime ?? 100) < 99 ? C.amber : undefined }}>{S.uptime ?? "–"}%</b></span>
        <span className="muted">{S.down_min ? `끊김 ${data.outages.length}번 · ${S.down_min}분${last ? ` · 마지막 ${when(last.start)}` : ""}` : "끊김 없음"}</span>
      </div>
      <div className="net-chart"><NetChart points={data.points} stepSec={data.step} /></div>
    </div>
  );
}

/** 아래쪽 상세: 프로세스 · 시스템 정보 · 디스크·GPU · Docker 를 탭 하나로 (공간 확보) */
function DetailCard({ M }: { M: Machine }) {
  const [tab, setTab] = useState<DetailTab>(() => store.get<DetailTab>("sys-detail", "proc"));
  const [pt, setPt] = useState<"cpu" | "mem">("cpu");
  const [nr, setNr] = useState<string>(() => store.get("net-range", "24h"));
  const pick = (t: DetailTab) => { setTab(t); store.set("sys-detail", t); };
  const cs = M.docker.containers, running = cs.filter((c) => c.state === "running").length;
  const gpuAlert = M.gpus.some((g) => (g.util ?? 0) >= 95 || (g.temp ?? 0) >= 85);
  const diskAlert = M.disks.some((d) => d.total && d.used / d.total >= 0.95);
  const dockAlert = cs.some((c) => c.state !== "running");
  const tabs: [DetailTab, string, boolean][] = [["proc", "프로세스", false], ["sys", "시스템", false],
    ["disk", "디스크·GPU", gpuAlert || diskAlert], ["docker", cs.length ? `Docker ${running}/${cs.length}` : "Docker", dockAlert]];
  const remote = !!M.id && M.id !== "local";
  if (remote) tabs.push(["net", "연결", !M.ok]);
  const cur: DetailTab = tab === "net" && !remote ? "proc" : tab;
  const procRows = pt === "cpu"
    ? M.topCpu.slice(0, 8).map((p, i) => <BarRow key={i} name={p.name} pct={p.cpu} value={`${(p.cpu ?? 0).toFixed(1)}%`} />)
    : M.topMem.slice(0, 8).map((p, i) => <BarRow key={i} name={p.name} pct={((p.mem ?? 0) / M.memTotal) * 300} value={fmtB(p.mem)} color={C.violet} warn={false} />);
  return (
    <Card className="m-detail" alert={gpuAlert || diskAlert || dockAlert} title={
      <span className="minitabs" style={{ marginLeft: 0 }} onPointerDown={(e) => e.stopPropagation()}>
        {tabs.map(([k, l, warn]) => <button key={k} className={cur === k ? "on" : ""} onClick={() => pick(k)}>{l}{warn && <i className="tdot" />}</button>)}
      </span>}
      right={cur === "net" ? (
        <span className="minitabs" onPointerDown={(e) => e.stopPropagation()}>
          {NET_RANGES.map(([k, l]) => <button key={k} className={nr === k ? "on" : ""} onClick={() => { setNr(k); store.set("net-range", k); }}>{l}</button>)}
        </span>) : cur === "proc" ? (
        <span className="minitabs" onPointerDown={(e) => e.stopPropagation()}>
          <button className={pt === "cpu" ? "on" : ""} onClick={() => setPt("cpu")}>CPU</button>
          <button className={pt === "mem" ? "on" : ""} onClick={() => setPt("mem")}>메모리</button>
        </span>) : cur === "disk" ? M.battery : null}>
      <div className={`m-detail-body ${cur === "net" ? "" : "scroll"}`}>
        {cur === "net" && remote && <NetPanel id={M.id!} rng={nr} />}
        {cur === "proc" && (procRows.length ? <div className="cols2">{procRows}</div> : <Muted>정보 없음</Muted>)}
        {cur === "sys" && (
          <div className="info-grid four">
            {M.info.map((it) => (
              <div key={it.label} className={it.value === NA ? "na" : ""}><span>{it.label}</span><b className="num">{it.value}</b></div>
            ))}
          </div>
        )}
        {cur === "disk" && (
          <div className="cols2">
            <div>
              <div className="sub-ttl">디스크</div>
              {M.disks.length ? M.disks.map((d, i) => <BarRow key={i} name={d.name} pct={(d.used / d.total) * 100} value={`${fmtB(d.total - d.used)} 남음`} />) : <Muted>정보 없음</Muted>}
            </div>
            <div>
              <div className="sub-ttl">GPU</div>
              {M.gpus.length ? M.gpus.map((g, i) => (
                <div key={i} style={{ marginBottom: ".4em" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: ".6em", margin: ".1em 0 .3em" }}>
                    <b style={{ fontSize: ".9em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{g.name}</b>
                    <span className="muted num">{g.temp != null ? `${g.temp}°C` : g.note || ""}</span>
                  </div>
                  {g.util != null && <BarRow name="사용률" pct={g.util} value={`${Math.round(g.util)}%`} color={C.green} />}
                  {g.memTotal ? <BarRow name="VRAM" pct={((g.memUsed || 0) / g.memTotal) * 100} value={`${fmtB(g.memUsed)} / ${fmtB(g.memTotal)}`} color={C.cyan} />
                    : g.sharedUsed ? <BarRow name="공유 메모리" pct={(g.sharedUsed / M.memTotal) * 100} value={fmtB(g.sharedUsed)} color={C.cyan} warn={false} /> : null}
                </div>
              )) : <Muted>정보 없음</Muted>}
            </div>
          </div>
        )}
        {cur === "docker" && (
          <>
            {cs.length ? cs.map((c, i) => (
              <div key={i} className={`ctr ${c.state === "running" ? "" : "down"}`}>
                <span className="dot" />
                <div style={{ minWidth: 0 }}><div className="n">{c.name}</div><div className="s">{c.image} · {c.status}</div></div>
                <div className="m num">{c.state === "running" ? <><b>{c.cpu.toFixed(1)}%</b> CPU<br /><b>{fmtB(c.mem)}</b></> : <b style={{ color: C.red }}>{c.state}</b>}</div>
              </div>
            )) : <Muted>{M.docker.error || "컨테이너 없음"}</Muted>}
            {M.wsl && (M.wsl.load || M.wsl.mem) && (
              <div className="divider">
                {M.wsl.load && <BarRow name={`WSL 부하 (${M.wsl.load.cpus}코어)`} pct={(M.wsl.load.l1 / M.wsl.load.cpus) * 100} value={M.wsl.load.l1.toFixed(2)} />}
                {M.wsl.mem && <BarRow name="WSL 메모리" pct={(M.wsl.mem.used / M.wsl.mem.total) * 100} value={`${fmtB(M.wsl.mem.used)} / ${fmtB(M.wsl.mem.total)}`} color={C.violet} />}
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

export function machineAlert(M: Machine) {
  return !M.ok || (M.cpu ?? 0) >= 90 || (M.mem ?? 0) >= 92 ||
    M.docker.containers.some((c) => c.state !== "running") ||
    M.gpus.some((g) => (g.util ?? 0) >= 95 || (g.temp ?? 0) >= 85);
}

export function MachineView({ M }: { M: Machine }) {
  const sysAlert = !M.ok || (M.cpu ?? 0) >= 90 || (M.mem ?? 0) >= 92;
  return (
    <>
      <Head icon={M.icon} title={M.title} sub={<span title={M.specFull}>{M.spec}</span>} tag={M.tag} tagCls={M.tagCls} />
      <div className="mbody">
      <Card alert={sysAlert} className="m-hero">
        <div className="hero">
          <Gauge pct={M.cpu} color={C.blue} label={`CPU ${M.cpuSub || ""}`} />
          <Gauge pct={M.mem} color={C.violet} label={`메모리 · ${M.memSub}`} />
        </div>
        {M.cpuPer.length > 0 && (
          <div className="corebox">
            <div className="corehead">
              <span>코어별 사용률 · {M.cpuPer.length}스레드{M.coreHist.length >= 2 ? ` · ${M.coreSpan}` : ""}</span>
              <span className="num">평균 {Math.round(M.cpuPer.reduce((a, b) => a + b, 0) / M.cpuPer.length)}% · 최고 {Math.round(Math.max(...M.cpuPer))}%</span>
            </div>
            {M.coreHist.length >= 2 ? <CoreHeatmap hist={M.coreHist} slots={M.coreSlots} cores={M.cpuPer.length} span={M.coreSpan} /> : (
              <div className="cores" style={{ gridTemplateColumns: `repeat(${M.cpuPer.length}, 1fr)` }}>
                {M.cpuPer.map((p, i) => <i key={i} title={`#${i} ${p}%`}><b style={{ height: `${Math.max(p, 2)}%`, background: level(p) || C.blue, opacity: 0.45 + p / 180 }} /></i>)}
              </div>
            )}
          </div>
        )}
        {M.err && <div className="err">{M.err}</div>}
      </Card>

      <HistoryCard M={M} />

      <DetailCard M={M} />
      </div>
    </>
  );
}
