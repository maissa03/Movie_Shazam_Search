"use client";

import { Info } from "lucide-react";
import { num, pct } from "@/lib/format";
import type { Hypothesis } from "@/lib/types";

export function ScoreGauge({ score, threshold, margin }: { score: number; threshold?: number; margin?: number | null }) {
  const angle = Math.max(0, Math.min(1, score)) * 180;
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4">
      <Gauge score={score} angle={angle} />
      <dl className="grid w-full grid-cols-2 gap-2 text-center text-xs">
        {threshold != null && (
          <div className="rounded-lg bg-ink-850 p-2">
            <dt className="text-slate-500">Seuil d’acceptation</dt>
            <dd className="mt-0.5 font-medium tabular-nums text-slate-200">{threshold.toFixed(2)}</dd>
          </div>
        )}
        {margin != null && (
          <div className="rounded-lg bg-ink-850 p-2">
            <dt className="text-slate-500">Marge / 2e</dt>
            <dd className="mt-0.5 font-medium tabular-nums text-slate-200">{pct(margin)}</dd>
          </div>
        )}
      </dl>
      <p className="text-center text-[11px] text-slate-500">0 = aucune ressemblance cohérente · 1 = clip identique au passage</p>
    </div>
  );
}

function Gauge({ score, angle }: { score: number; angle: number }) {
  return (
    <div className="relative mx-auto h-24 w-48" aria-label={`Score de similarité ${score.toFixed(2)} sur 1`}>
      <svg viewBox="0 0 200 105" className="h-full w-full">
        <path d="M10 100 A90 90 0 0 1 190 100" fill="none" stroke="#243041" strokeWidth="14" strokeLinecap="round" />
        <path
          d="M10 100 A90 90 0 0 1 190 100"
          fill="none"
          stroke="#f5b544"
          strokeWidth="14"
          strokeLinecap="round"
          strokeDasharray={`${(angle / 180) * 283} 283`}
        />
      </svg>
      <div className="absolute inset-x-0 bottom-0 text-center">
        <div className="text-3xl font-semibold tabular-nums text-white">{score.toFixed(2)}</div>
        <div className="text-[11px] uppercase tracking-wider text-slate-400">score de similarité</div>
      </div>
    </div>
  );
}

export function ScoreBreakdown({ h, margin, maxHamming }: { h: Hypothesis; margin?: number | null; maxHamming: number }) {
  const rows: [string, string, string][] = [
    ["Images cohérentes", `${h.matched_frames} / ${h.expected_frames}`, "images de la grille du film retrouvées au bon instant du clip"],
    ["Couverture", pct(h.coverage), "part du clip expliquée par l'alignement"],
    ["Étendue temporelle", pct(h.span_ratio), "du premier au dernier instant aligné"],
    ["Distance de Hamming moyenne", `${num(h.mean_distance, 1)} / 64 bits`, `0 = identique ; seuil d'acceptation d'une paire : ${maxHamming}`],
    ["Stabilité du décalage", `± ${num(h.offset_std_ms)} ms`, "écart-type de (t_film − t_clip) sur les images alignées"],
    ["Images hors ordre écartées", String(h.order_rejected), "vérification de l'ordre temporel"],
  ];
  if (margin != null) rows.push(["Marge sur le 2e candidat", pct(margin), "1 − score₂ / score₁ ; une marge faible rend le résultat ambigu"]);
  return (
    <div>
      <dl className="divide-y divide-ink-700/60 text-sm">
        {rows.map(([k, v, help]) => (
          <div key={k} className="flex items-baseline justify-between gap-4 py-2">
            <dt className="text-slate-400" title={help}>
              {k}
            </dt>
            <dd className="text-right font-medium tabular-nums text-slate-100">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 flex gap-2 rounded-lg bg-ink-850 p-3 text-xs leading-relaxed text-slate-400">
        <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        <span>
          Score = moyenne des similarités (1 − d/(D+1)) sur les images attendues × facteur de stabilité. C’est un{" "}
          <strong className="text-slate-200">indicateur heuristique</strong>, pas une probabilité : il n’a pas été calibré
          statistiquement.
        </span>
      </p>
    </div>
  );
}
