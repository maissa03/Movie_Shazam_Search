"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Activity, Ban, Cpu, Fingerprint, HardDrive, Layers } from "lucide-react";
import { api } from "@/lib/api";
import { dateTime, duration, num } from "@/lib/format";
import type { Job } from "@/lib/types";
import { usePolling } from "@/lib/usePolling";
import { EmptyState, ErrorBox, PageHeader, ProgressBar, StatCard, StatusBadge } from "@/components/ui";

const KIND: Record<Job["kind"], string> = { index: "Indexation", search: "Recherche" };

export default function JobsPage() {
  const [kind, setKind] = useState("");
  const fetcher = useMemo(() => () => api.jobs(kind || undefined), [kind]);
  const { data: jobs, error, refresh } = usePolling(fetcher, 1000);
  const { data: health } = usePolling(api.health, 10_000);
  const [cancelling, setCancelling] = useState<string | null>(null);

  const active = jobs?.filter((j) => j.state === "queued" || j.state === "running") ?? [];
  const totalFp = jobs?.filter((j) => j.kind === "index").reduce((a, j) => a + j.fingerprints, 0) ?? 0;
  const pidsInUse = [...new Set(active.flatMap((j) => j.worker_pids))];

  const cancel = async (j: Job) => {
    setCancelling(j.id);
    try {
      await api.cancelJob(j.id);
      refresh();
    } finally {
      setCancelling(null);
    }
  };

  return (
    <>
      <PageHeader title="Traitements" subtitle="Jobs locaux d’indexation et de recherche : progression réelle (segments terminés / total), processus CPU utilisés, erreurs et annulation coopérative." />

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Pool de processus" icon={<Cpu className="size-4" />} value={health?.concurrency.process_workers ?? "—"} hint={health ? `PID ${health.concurrency.worker_pids.join(", ")}` : undefined} />
        <StatCard label="Threads E/S" icon={<HardDrive className="size-4" />} value={health?.concurrency.io_threads ?? "—"} hint="copie, SHA-256, ffprobe, vignettes" />
        <StatCard label="Jobs actifs" icon={<Activity className="size-4" />} value={active.length} hint={pidsInUse.length ? `processus actifs : ${pidsInUse.join(", ")}` : `max ${health?.concurrency.max_active_index_jobs ?? 1} indexation(s) à la fois`} />
        <StatCard label="Empreintes calculées" icon={<Fingerprint className="size-4" />} value={num(totalFp)} hint="jobs d’indexation listés" />
      </section>

      <div className="mt-6 flex items-center gap-2">
        {[
          ["", "Tous"],
          ["index", "Indexations"],
          ["search", "Recherches"],
        ].map(([v, l]) => (
          <button key={v} onClick={() => setKind(v)} className={`rounded-full px-3 py-1 text-xs transition ${kind === v ? "bg-accent text-ink-950" : "bg-ink-850 text-slate-400 hover:text-slate-100"}`}>
            {l}
          </button>
        ))}
      </div>

      <div className="mt-4 space-y-3">
        {error && <ErrorBox message={error} onRetry={refresh} />}
        {jobs && jobs.length === 0 && (
          <EmptyState icon={<Layers className="size-5" />} title="Aucun traitement">
            Les jobs apparaissent ici dès qu’un film est importé ou qu’une recherche est lancée.
          </EmptyState>
        )}
        {jobs?.map((j) => (
          <article key={j.id} className="card p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="rounded-md bg-ink-800 px-2 py-0.5 text-[11px] uppercase tracking-wider text-slate-400">{KIND[j.kind]}</span>
                  {j.movie_id ? (
                    <Link href={`/bibliotheque/${j.movie_id}`} className="truncate font-medium text-slate-100 hover:text-accent">
                      {j.label}
                    </Link>
                  ) : j.search_id ? (
                    <Link href={`/reconnaissance?search=${j.search_id}`} className="truncate font-medium text-slate-100 hover:text-accent">
                      {j.label}
                    </Link>
                  ) : (
                    <span className="truncate font-medium text-slate-100">{j.label}</span>
                  )}
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  {dateTime(j.created_at)} · {j.stage ?? "—"}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <StatusBadge status={j.state} />
                {(j.state === "queued" || j.state === "running") && (
                  <button className="btn-danger px-3 py-1 text-xs" disabled={cancelling === j.id || j.cancel_requested} onClick={() => cancel(j)}>
                    <Ban className="size-3.5" /> {j.cancel_requested ? "Annulation…" : "Annuler"}
                  </button>
                )}
              </div>
            </div>
            <div className="mt-3">
              <ProgressBar value={j.completed_units} total={j.total_units || 1} active={j.state === "running"} tone={j.state === "failed" ? "rose" : j.state === "completed" ? "emerald" : "accent"} />
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-400 sm:grid-cols-3 lg:grid-cols-6">
              <div>
                <dt className="text-slate-500">Progression</dt>
                <dd className="tabular-nums text-slate-200">
                  {j.completed_units}/{j.total_units} {j.kind === "index" ? "segments" : "étapes"}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">Processus utilisés</dt>
                <dd className="tabular-nums text-slate-200" title={j.worker_pids.join(", ")}>
                  {j.kind === "search" ? "thread + FFmpeg" : j.workers_used ? `${j.workers_used} (PID ${j.worker_pids.join(", ")})` : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">Empreintes</dt>
                <dd className="tabular-nums text-slate-200">{num(j.fingerprints)}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Images décodées</dt>
                <dd className="tabular-nums text-slate-200">{num(j.frames_decoded)}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Durée</dt>
                <dd className="tabular-nums text-slate-200">{duration(j.elapsed_ms)}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Segments réutilisés</dt>
                <dd className="tabular-nums text-slate-200">{j.kind === "index" ? String(j.details?.segments_reused ?? "—") : "—"}</dd>
              </div>
            </dl>
            {j.error && <p className="mt-3 rounded-lg bg-rose-500/10 p-2 text-xs text-rose-200">{j.error}</p>}
          </article>
        ))}
      </div>
    </>
  );
}
