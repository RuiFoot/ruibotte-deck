import type { ReactNode } from "react";
import { C, gaugeStops, level, scaleColor } from "../lib";

export function Card({ title, right, children, className = "", alert = false }:
  { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; alert?: boolean }) {
  return (
    <div className={`card ${className} ${alert ? "alert" : ""}`}>
      {title != null && <div className="ttl">{title}{right != null && <span className="r num">{right}</span>}</div>}
      {children}
    </div>
  );
}

/** 게이지 빛 번짐용 반투명 색 (#hex 또는 rgb() 모두 지원) */
const glow = (col: string) => col.startsWith("#") ? `${col}55` : col.replace("rgb(", "rgba(").replace(")", ",0.35)");

/** 원형 게이지. useLevel 이면 자기 색 → 50%부터 공통 주황 → 90% 빨강 */
export function Ring({ pct, color, useLevel = true, className }: { pct?: number | null; color: string; useLevel?: boolean; className?: string }) {
  const r = 40, c = 2 * Math.PI * r, p = Math.max(0, Math.min(100, pct || 0));
  const col = useLevel && color.startsWith("#") ? scaleColor(gaugeStops(color), p / 100) : color;
  return (
    <svg viewBox="0 0 100 100" className={className}>
      <circle cx="50" cy="50" r={r} fill="none" stroke={C.panel2} strokeWidth="9" />
      <circle cx="50" cy="50" r={r} fill="none" stroke={col} strokeWidth="9" strokeLinecap="round"
        strokeDasharray={`${(c * p) / 100} ${c}`} transform="rotate(-90 50 50)"
        style={{ transition: "stroke-dasharray .6s, stroke .3s", filter: `drop-shadow(0 0 4px ${glow(col)})` }} />
    </svg>
  );
}

export function Gauge({ pct, color, label }: { pct?: number | null; color: string; label: ReactNode }) {
  return (
    <div className="gauge">
      <Ring pct={pct} color={color} />
      <div style={{ minWidth: 0 }}>
        <div className="v num">{pct == null ? "–" : Math.round(pct)}<small>%</small></div>
        <div className="l">{label}</div>
      </div>
    </div>
  );
}

export function BarRow({ name, pct, value, color = C.blue, warn = true }:
  { name: ReactNode; pct?: number | null; value: ReactNode; color?: string; warn?: boolean }) {
  const p = Math.min(100, Math.max(0, pct || 0));
  return (
    <div className="row">
      <span className="n">{name}</span>
      <span className="bar"><i style={{ width: `${p}%`, background: (warn && level(p)) || color }} /></span>
      <span className="v num">{value}</span>
    </div>
  );
}

export const Muted = ({ children }: { children: ReactNode }) => <div className="muted">{children}</div>;

const svgProps = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const };
export const Icon = {
  laptop: <svg {...svgProps}><rect x="4" y="5" width="16" height="11" rx="2" /><path d="M2 19h20" /></svg>,
  server: <svg {...svgProps}><rect x="3" y="4" width="18" height="7" rx="2" /><rect x="3" y="13" width="18" height="7" rx="2" /><path d="M7 7.5h.01M7 16.5h.01" /></svg>,
  monitor: <svg {...svgProps}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></svg>,
  spark: <svg {...svgProps}><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4" /></svg>,
  gear: <svg {...svgProps}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>,
};

export function Head({ icon, title, sub, tag, tagCls }: { icon: ReactNode; title: ReactNode; sub?: ReactNode; tag?: ReactNode; tagCls?: string }) {
  return (
    <div className="mhead">
      <div className="ic">{icon}</div>
      <div style={{ minWidth: 0 }}>
        <div className="t">{title}</div>
        {sub != null && <div className="s">{sub}</div>}
      </div>
      {tag != null && <span className={`tag ${tagCls || ""}`}>{tag}</span>}
    </div>
  );
}
