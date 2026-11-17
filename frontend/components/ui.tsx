"use client";

import { AlertTriangle, CheckCircle2, CircleDashed, Clock, Loader2, XCircle } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

const STATUS: Record<string, { label: string; cls: string; icon: typeof CheckCircle2 }> = {
  indexed: { label: "Indexé", cls: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30", icon: CheckCircle2 },
  completed: { label: "Terminé", cls: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30", icon: CheckCircle2 },
  match: { label: "Trouvé", cls: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30", icon: CheckCircle2 },
  done: { label: "Terminé", cls: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30", icon: CheckCircle2 },
  indexing: { label: "Indexation", cls: "bg-sky-500/10 text-sky-300 ring-sky-500/30", icon: Loader2 },
  running: { label: "En cours", cls: "bg-sky-500/10 text-sky-300 ring-sky-500/30", icon: Loader2 },
  queued: { label: "En attente", cls: "bg-slate-500/10 text-slate-300 ring-slate-500/30", icon: Clock },
  pending: { label: "En attente", cls: "bg-slate-500/10 text-slate-300 ring-slate-500/30", icon: Clock },
  ambiguous: { label: "Ambigu", cls: "bg-amber-500/10 text-amber-300 ring-amber-500/30", icon: AlertTriangle },
  no_match: { label: "Aucun résultat", cls: "bg-slate-500/10 text-slate-300 ring-slate-500/30", icon: CircleDashed },
  insufficient: { label: "Peu informatif", cls: "bg-slate-500/10 text-slate-300 ring-slate-500/30", icon: CircleDashed },
  failed: { label: "Échec", cls: "bg-rose-500/10 text-rose-300 ring-rose-500/30", icon: XCircle },
  cancelled: { label: "Annulé", cls: "bg-zinc-500/10 text-zinc-300 ring-zinc-500/30", icon: XCircle },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, cls: "bg-slate-500/10 text-slate-300 ring-slate-500/30", icon: CircleDashed };
  const Icon = s.icon;
  const spinning = status === "indexing" || status === "running";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${s.cls}`}>
      <Icon className={`size-3.5 ${spinning ? "animate-spin" : ""}`} aria-hidden />
      {s.label}
    </span>
  );
}

export function ProgressBar({ value, total, active, tone = "accent" }: { value: number; total: number; active?: boolean; tone?: "accent" | "sky" | "emerald" | "rose" }) {
  const frac = total > 0 ? Math.min(1, value / total) : 0;
  const color = { accent: "bg-accent", sky: "bg-sky-400", emerald: "bg-emerald-400", rose: "bg-rose-400" }[tone];
  return (
    <div
      className="relative h-2 w-full overflow-hidden rounded-full bg-ink-700/70"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={value}
    >
      <div className={`h-full rounded-full ${color} transition-[width] duration-500`} style={{ width: `${frac * 100}%` }} />
      {active && <div className="animate-shimmer absolute inset-0" />}
    </div>
  );
}

export function StatCard({ label, value, hint, icon }: { label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <span className="label">{label}</span>
        <span className="text-slate-500">{icon}</span>
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums text-white">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-400">{hint}</div>}
    </div>
  );
}

export function EmptyState({ icon, title, children, action }: { icon: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center gap-3 px-6 py-12 text-center">
      <div className="grid size-12 place-items-center rounded-2xl bg-ink-800 text-slate-400">{icon}</div>
      <h3 className="text-base font-semibold text-slate-100">{title}</h3>
      {children && <div className="max-w-md text-sm text-slate-400">{children}</div>}
      {action}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-100">
      <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="flex-1">{message}</div>
      {onRetry && (
        <button className="text-rose-200 underline underline-offset-2 hover:text-white" onClick={onRetry}>
          Réessayer
        </button>
      )}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-xl bg-ink-800 ${className}`} />;
}

export function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-200">{children}</dd>
    </div>
  );
}
