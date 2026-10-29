// 사용량 상세: 30일·90일 막대, 큰 잔디(13주), 기간 합계·모델별·PC별
import { useState } from "react";
import type { ProviderSummary } from "../types";
import { fmtT, providerColor, usePoll } from "../lib";
import { Muted } from "./ui";
import { Overlay } from "./SessionModal";
import { Grass, SeriesBars } from "./charts";

export function UsageDetail({ pid, label, machine, machineName, names, onClose }: {
  pid: string; label: string; machine?: string; machineName?: string; names: (id?: string) => string; onClose: () => void;
}) {
  const [rng, setRng] = useState<"30d" | "90d">("30d");
  const q = (r: string) => `/api/providers/${pid}/summary?range=${r}${machine ? `&machine=${encodeURIComponent(machine)}` : ""}`;
  const { data: S } = usePoll<ProviderSummary>(q(rng), 60000);
  const { data: G } = usePoll<ProviderSummary>(q("90d"), 300000);
  const col = providerColor(pid);
  const series = S?.series || [];
  const total = series.reduce((a, p) => a + p.total, 0);
  const days = series.filter((p) => p.total > 0).length;
  const models = Object.entries(S?.models_range || {}).sort((a, b) => b[1] - a[1]);
  const mtot = models.reduce((a, [, v]) => a + v, 0) || 1;
  const pcs = Object.entries(S?.machines_range || {}).sort((a, b) => b[1].total - a[1].total);
  return (
    <Overlay wide title={<>{label} 사용량 상세{machineName ? ` · ${machineName}` : " · 전체 PC"}</>} onClose={onClose}>
      <div className="filters">
        <div className="minitabs">
          {(["30d", "90d"] as const).map((r) => <button key={r} className={rng === r ? "on" : ""} onClick={() => setRng(r)}>{r === "30d" ? "30일" : "90일"}</button>)}
        </div>
        <span className="muted num">합계 <b>{fmtT(total)}</b> · 사용한 날 {days}일 · 하루 평균 {fmtT(days ? total / days : 0)}</span>
      </div>
      {!S ? <Muted>불러오는 중…</Muted> : <SeriesBars series={series} days={rng === "30d" ? 30 : 90} stops={col.stops} height="12em" />}
      <div className="detail-grid">
        <div className="card">
          <div className="ttl">잔디 · 최근 13주</div>
          {G ? <Grass series={G.series || []} weeks={13} stops={col.stops} cell="1.35em" /> : <Muted>불러오는 중…</Muted>}
        </div>
        <div className="card">
          <div className="ttl">모델별 ({rng === "30d" ? "30일" : "90일"})</div>
          {models.length ? models.map(([m, v]) => (
            <div key={m} className="row"><span className="n">{m}</span>
              <span className="bar"><i style={{ width: `${(100 * v) / mtot}%`, background: col.accent }} /></span>
              <span className="v num">{fmtT(v)}</span></div>
          )) : <Muted>기록 없음</Muted>}
          {!machine && pcs.length > 1 && (
            <>
              <div className="ttl" style={{ marginTop: "1em" }}>PC별</div>
              {pcs.map(([m, v]) => (
                <div key={m} className="row"><span className="n">{names(m)}</span>
                  <span className="bar"><i style={{ width: `${(100 * v.total) / (total || 1)}%`, background: col.accent }} /></span>
                  <span className="v num">{fmtT(v.total)}</span></div>
              ))}
            </>
          )}
        </div>
      </div>
    </Overlay>
  );
}
