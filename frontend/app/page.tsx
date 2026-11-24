"use client";

import Link from "next/link";
import { Activity, ArrowRight, Clock, Fingerprint, Library, ScanSearch, Server } from "lucide-react";
import { api } from "@/lib/api";
import { duration, hms, num, relative } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import { EmptyState, ErrorBox, PageHeader, ProgressBar, Skeleton, StatCard, StatusBadge } from "@/components/ui";

export default function Dashboard() {
  const { data: stats, error, refresh } = usePolling(api.stats, 2000);
  const { data: health } = usePolling(api.health, 15_000);

  return (
    <>
      <PageHeader
        title="Tableau de bord"
        subtitle="Retrouvez le film et l’instant exact d’un extrait vidéo parmi les films de votre bibliothèque locale."
        actions={
          <Link href="/reconnaissance" className="btn-primary">
            <ScanSearch className="size-4" aria-hidden /> Identifier un film
          </Link>
        }
      />
      {error && <div className="mb-6"><ErrorBox message={error} onRetry={refresh} /></div>}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {stats ? (
          <>
            <StatCard label="Films" icon={<Library className="size-4" />} value={`${stats.movies_indexed} / ${stats.movies_total}`} hint="indexés / importés" />
            <StatCard label="Durée indexée" icon={<Clock className="size-4" />} value={hms(stats.indexed_duration_ms)} hint={duration(stats.indexed_duration_ms)} />
            <StatCard label="Empreintes" icon={<Fingerprint className="size-4" />} value={num(stats.fingerprints_total)} hint="pHash 64 bits en base SQLite" />
            <StatCard label="Traitements actifs" icon={<Activity className="size-4" />} value={stats.active_jobs.length} hint={`${stats.process_workers} processus CPU locaux`} />
          </>
        ) : (
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-28" />)
        )}
      </section>

      <div className="mt-6 grid gap-6 lg:grid-cols-5">
        <section className="card p-5 lg:col-span-3">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-white">Traitements en cours</h2>
            <Link href="/traitements" className="text-xs text-slate-400 hover:text-accent">
              Tout voir
            </Link>
          </div>
          {stats && stats.active_jobs.length === 0 && (
            <p className="rounded-xl bg-ink-850 p-4 text-sm text-slate-400">Aucun traitement en cours. Importez un film dans la bibliothèque pour lancer une indexation.</p>
          )}
          <ul className="space-y-4">
            {stats?.active_jobs.map((j) => (
              <li key={j.id} className="animate-fade-in">
                <div className="mb-1 flex items-center justify-between gap-3 text-sm">
                  <span className="truncate text-slate-200">
                    {j.kind === "index" ? "Indexation" : "Recherche"} · {j.label}
                  </span>
                  <StatusBadge status={j.state} />
                </div>
                <ProgressBar value={j.completed_units} total={j.total_units} active={j.state === "running"} />
                <div className="mt-1 flex justify-between text-xs text-slate-500">
                  <span className="truncate">{j.stage}</span>
                  <span className="tabular-nums">
                    {j.completed_units}/{j.total_units} {j.kind === "index" ? "segments" : "étapes"}
                    {j.workers_used > 0 && ` · ${j.workers_used} processus`}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="card p-5 lg:col-span-2">
          <h2 className="mb-4 font-semibold text-white">Dernières recherches</h2>
          {stats && stats.recent_searches.length === 0 ? (
            <EmptyState icon={<ScanSearch className="size-5" />} title="Aucune recherche" action={<Link href="/reconnaissance" className="btn-ghost">Essayer avec un clip <ArrowRight className="size-4" /></Link>}>
              Déposez un extrait de 5 à 30 secondes pour retrouver son film.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-ink-700/60">
              {stats?.recent_searches.map((s) => (
                <li key={s.id}>
                  <Link href={`/reconnaissance?search=${s.id}`} className="flex items-center justify-between gap-3 py-2.5 hover:text-accent">
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-slate-200">{s.clip_name}</span>
                      <span className="block truncate text-xs text-slate-500">
                        {s.best_title ? `${s.best_title} · ${hms(s.start_ms)}` : "—"} · {relative(s.created_at)}
                      </span>
                    </span>
                    <StatusBadge status={s.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {health && (
        <section className="card mt-6 flex flex-wrap items-center gap-x-6 gap-y-2 p-4 text-xs text-slate-400">
          <span className="flex items-center gap-2 text-slate-200">
            <Server className="size-4 text-accent" aria-hidden /> Backend Python {health.python}
          </span>
          <span>FFmpeg : {health.ffmpeg.available ? health.ffmpeg.version?.split(" ").slice(0, 3).join(" ") : "introuvable"}</span>
          <span>
            Pool : {health.concurrency.process_workers} processus (PID {health.concurrency.worker_pids.join(", ")}) sur {health.concurrency.cpu_count} cœurs logiques
          </span>
          <span>SQLite WAL · écrivain unique {health.database.writer_alive ? "actif" : "arrêté"}</span>
          <span>Disque libre : {health.limits.disk_free_gb} Go</span>
        </section>
      )}
    </>
  );
}
