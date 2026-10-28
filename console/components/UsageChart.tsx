// Usage per day as thin stacked bars (ingest under recall), with a per-bar hover
// tooltip and a table view for screen readers and exact values.
import type { UsageDay } from "@/lib/types";

const W = 720, H = 200, PAD_L = 44, PAD_B = 24, PAD_T = 8;

function niceMax(v: number) {
  const step = 10 ** Math.floor(Math.log10(Math.max(v, 1)));
  return Math.max(2, Math.ceil(v / step) * step);
}

export function UsageChart({ days, unit = "calls" }: { days: Pick<UsageDay, "date" | "ingest" | "recall">[]; unit?: string }) {
  if (days.length === 0) {
    return <p className="py-10 text-center text-[13px] text-text-3">No usage in the last 30 days.</p>;
  }
  const max = niceMax(Math.max(0, ...days.map((d) => d.ingest + d.recall)));
  const plotW = W - PAD_L, plotH = H - PAD_B - PAD_T;
  const slot = plotW / Math.max(1, days.length);
  const barW = Math.max(4, Math.min(14, slot - 6));
  const y = (v: number) => PAD_T + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];

  return (
    <div>
      <div className="mb-3 flex items-center gap-4 text-[12px] text-text-2">
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-sm bg-[var(--chart)]" /> Ingest</span>
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-sm bg-[var(--chart-2)]" /> Recall</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`${unit} per day, last 30 days`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD_L} x2={W} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth="1" strokeDasharray={t === 0 ? undefined : "2 4"} />
            <text x={PAD_L - 10} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--text-3)" className="tabular-nums">{t.toLocaleString()}</text>
          </g>
        ))}
        {days.map((d, i) => {
          const x = PAD_L + i * slot + (slot - barW) / 2;
          const ingestTop = y(d.ingest);
          const totalTop = y(d.ingest + d.recall);
          const label = `${d.date}: ${d.ingest.toLocaleString()} ingest + ${d.recall.toLocaleString()} recall ${unit}`;
          return (
            <g key={d.date} className="group">
              <title>{label}</title>
              {/* Hit target taller and wider than the bar. */}
              <rect x={PAD_L + i * slot} y={PAD_T} width={slot} height={plotH} fill="transparent" />
              <rect x={PAD_L + i * slot + 1} y={PAD_T} width={slot - 2} height={plotH} rx="3" fill="var(--text)" opacity="0" className="group-hover:opacity-[0.05]" />
              <rect x={x} y={ingestTop} width={barW} height={y(0) - ingestTop} fill="var(--chart)" />
              <rect x={x} y={totalTop} width={barW} height={Math.max(0, ingestTop - totalTop - 2)} rx="2" fill="var(--chart-2)" />
              {i % 7 === days.length % 7 && (
                <text x={x + barW / 2} y={H - 6} textAnchor="middle" fontSize="10" fill="var(--text-3)">{d.date.slice(5)}</text>
              )}
            </g>
          );
        })}
      </svg>
      <details className="mt-3 text-[13px]">
        <summary className="inline-block cursor-pointer text-[12px] text-text-3 hover:text-text">Show as table</summary>
        <table className="mt-2 w-full text-left text-[12px] tabular-nums">
          <thead className="text-text-3"><tr><th className="py-1 font-medium">Date</th><th className="font-medium">Ingest</th><th className="font-medium">Recall</th></tr></thead>
          <tbody>
            {[...days].reverse().map((d) => (
              <tr key={d.date} className="border-t border-border"><td className="py-1">{d.date}</td><td>{d.ingest.toLocaleString()}</td><td>{d.recall.toLocaleString()}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
