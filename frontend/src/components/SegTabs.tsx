import { useLayoutEffect, useRef, useState } from "react";

export interface SegItem { label: string; sub: string; warn?: boolean }

/** 하위 탭. progressKey가 바뀔 때마다 자동 넘김 진행 줄이 처음부터 다시 참 (running=false면 숨김) */
export function SegTabs({ items, index, onSelect, running, durationSec, progressKey }:
  { items: SegItem[]; index: number; onSelect: (i: number) => void; running: boolean; durationSec: number; progressKey: number }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const [pos, setPos] = useState({ left: 0, width: 0 });
  useLayoutEffect(() => {
    const update = () => { const b = refs.current[index]; if (b) setPos({ left: b.offsetLeft, width: b.offsetWidth }); };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [index, items.map((i) => i.sub).join("|")]);
  return (
    <nav className="seg">
      <div className="slider" style={pos}>
        {running && <i key={progressKey} style={{ animation: `segprog ${durationSec}s linear forwards` }} />}
      </div>
      {items.map((it, i) => (
        <button key={i} ref={(el) => { refs.current[i] = el; }} className={i === index ? "on" : ""} onClick={() => onSelect(i)}>
          <b>{it.label}{it.warn && <i className="badge" />}</b>
          <span className="num">{it.sub}</span>
        </button>
      ))}
    </nav>
  );
}
