"use client";

import { hms } from "@/lib/format";

export function Timeline({
  durationMs,
  startMs,
  endMs,
  aligned = [],
  maxHamming = 12,
  tone = "accent",
}: {
  durationMs: number;
  startMs: number;
  endMs: number;
  aligned?: [number, number, number][];
  maxHamming?: number;
  tone?: "accent" | "amber";
}) {
  const d = Math.max(durationMs, endMs, 1);
  const left = (startMs / d) * 100;
  const width = Math.max(((endMs - startMs) / d) * 100, 0.6);
  const color = tone === "accent" ? "bg-accent" : "bg-amber-400";
  return (
    <div>
      <div className="relative h-10 overflow-hidden rounded-xl bg-ink-800 ring-1 ring-ink-700" aria-label={`Passage de ${hms(startMs)} à ${hms(endMs)} sur ${hms(d)}`}>
        {Array.from({ length: 9 }, (_, i) => (
          <div key={i} className="absolute inset-y-0 w-px bg-ink-700/60" style={{ left: `${(i + 1) * 10}%` }} />
        ))}
        <div className={`absolute inset-y-1 rounded-md ${color} opacity-90 shadow-[0_0_18px] shadow-accent/40`} style={{ left: `${left}%`, width: `${width}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-[11px] tabular-nums text-slate-500">
        <span>00:00:00</span>
        <span className="text-slate-300">
          {hms(startMs)} → {hms(endMs)}
        </span>
        <span>{hms(d)}</span>
      </div>
      {aligned.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 flex items-center justify-between text-[11px] text-slate-500">
            <span>Images alignées dans le passage ({aligned.length})</span>
            <span className="flex items-center gap-2">
              <span className="inline-block h-2 w-3 rounded-sm bg-emerald-400" /> d=0
              <span className="inline-block h-2 w-3 rounded-sm bg-amber-400" /> d≈{Math.round(maxHamming / 2)}
              <span className="inline-block h-2 w-3 rounded-sm bg-rose-400" /> d={maxHamming}
            </span>
          </div>
          <div className="relative h-6 rounded-lg bg-ink-850 ring-1 ring-ink-700">
            {aligned.map(([, tFilm, dist], i) => {
              const pos = ((tFilm - startMs) / Math.max(endMs - startMs, 1)) * 100;
              const ratio = dist / maxHamming;
              const c = ratio < 0.34 ? "bg-emerald-400" : ratio < 0.67 ? "bg-amber-400" : "bg-rose-400";
              return (
                <div
                  key={i}
                  title={`film ${hms(tFilm, true)} · distance ${dist}`}
                  className={`absolute top-1 bottom-1 w-[3px] rounded-full ${c}`}
                  style={{ left: `calc(${Math.min(99.5, Math.max(0, pos))}% - 1px)` }}
                />
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
