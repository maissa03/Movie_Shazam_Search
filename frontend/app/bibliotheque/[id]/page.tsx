"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Check, Cpu, Pencil, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import { api, mediaUrl } from "@/lib/api";
import { bytes, dateTime, duration, hms, num } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import { MovieThumb } from "@/components/MovieThumb";
import { ErrorBox, Meta, PageHeader, ProgressBar, Skeleton, StatusBadge } from "@/components/ui";

const PID_COLORS = ["bg-sky-400", "bg-violet-400", "bg-emerald-400", "bg-pink-400", "bg-orange-400", "bg-teal-300", "bg-lime-300", "bg-fuchsia-400"];

export default function MovieDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = Number(params.id);
  const fetcher = useMemo(() => () => api.movie(id), [id]);
  const [polling, setPolling] = useState(true);
  const { data: m, error, refresh } = usePolling(fetcher, polling ? 1500 : 10_000);
  const [msg, setMsg] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [year, setYear] = useState("");
  useEffect(() => {
    if (m) setPolling(m.status === "indexing" || m.status === "pending" || !!m.active_job_id);
  }, [m]);

  const pids = useMemo(() => [...new Set((m?.segments ?? []).map((s) => s.worker_pid).filter((p): p is number => p != null))], [m]);

  const act = async (fn: () => Promise<{ message?: string }>) => {
    try {
      const r = await fn();
      setMsg(r.message ?? "OK");
      refresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  if (error && !m) return <ErrorBox message={error} onRetry={refresh} />;
  if (!m) return <Skeleton className="h-96" />;

  return (
    <>
      <Link href="/bibliotheque" className="mb-4 inline-flex items-center gap-1 text-sm text-slate-400 hover:text-accent">
        <ArrowLeft className="size-4" /> Bibliothèque
      </Link>
      {editing && (
        <form
          className="card mb-6 flex flex-col gap-3 p-4 sm:flex-row sm:items-end"
          onSubmit={async (e) => {
            e.preventDefault();
            await act(async () => {
              await api.updateMovie(m.id, year ? { title, year: Number(year) } : { title, clear_year: true });
              setEditing(false);
              return { message: "Métadonnées enregistrées." };
            });
          }}
        >
          <label className="flex-1">
            <span className="label">Titre</span>
            <input className="input mt-1" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} />
          </label>
          <label className="sm:w-32">
            <span className="label">Année</span>
            <input className="input mt-1" value={year} onChange={(e) => setYear(e.target.value.replace(/D/g, "").slice(0, 4))} placeholder="facultative" inputMode="numeric" />
          </label>
          <div className="flex gap-2">
            <button className="btn-primary" type="submit"><Check className="size-4" /> Enregistrer</button>
            <button className="btn-ghost" type="button" onClick={() => setEditing(false)}><X className="size-4" /></button>
          </div>
        </form>
      )}
      <PageHeader
        title={m.title}
        subtitle={[m.year, m.original_filename].filter(Boolean).join(" · ")}
        actions={
          <>
            <button className="btn-ghost" onClick={() => { setTitle(m.title); setYear(m.year ? String(m.year) : ""); setEditing(true); }}>
              <Pencil className="size-4" /> Modifier
            </button>
            <button className="btn-ghost" disabled={!!m.active_job_id} onClick={() => act(() => api.reindex(m.id, false))}>
              <RefreshCw className="size-4" /> Réindexer (incrémental)
            </button>
            <button className="btn-ghost" disabled={!!m.active_job_id} onClick={() => confirm("Tout recalculer (index supprimé puis reconstruit) ?") && act(() => api.reindex(m.id, true))}>
              <RotateCcw className="size-4" /> Tout recalculer
            </button>
            <button
              className="btn-danger"
              onClick={async () => {
                if (!confirm(`Supprimer « ${m.title} » et toutes ses empreintes ?`)) return;
                await api.deleteMovie(m.id);
                router.push("/bibliotheque");
              }}
            >
              <Trash2 className="size-4" /> Supprimer
            </button>
          </>
        }
      />
      {msg && <p className="mb-4 rounded-xl border border-ink-700 bg-ink-850 px-4 py-2 text-sm text-slate-300">{msg}</p>}

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="card overflow-hidden lg:col-span-3">
          {m.playable_in_browser ? (
            <video src={mediaUrl.video(m.id)} controls preload="metadata" poster={m.has_thumbnail ? mediaUrl.thumbnail(m.id) : undefined} className="aspect-video w-full bg-black" />
          ) : (
            <MovieThumb src={m.has_thumbnail ? mediaUrl.thumbnail(m.id) : null} alt={m.title} className="aspect-video w-full" />
          )}
        </div>
        <div className="card p-5 lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-white">Métadonnées</h2>
            <StatusBadge status={m.status} />
          </div>
          <dl className="grid grid-cols-2 gap-4">
            <Meta label="Durée">{hms(m.duration_ms)} ({duration(m.duration_ms)})</Meta>
            <Meta label="Résolution">{m.width}×{m.height}</Meta>
            <Meta label="Codec · images/s">{m.codec} · {m.fps}</Meta>
            <Meta label="Conteneur">{(m.original_filename.split(".").pop() ?? "").toUpperCase()} <span className="text-xs text-slate-500">({m.format_name.split(",").slice(0, 2).join(", ")})</span></Meta>
            <Meta label="Taille">{bytes(m.file_size)}</Meta>
            <Meta label="Empreintes">{num(m.fingerprints_count)}</Meta>
            <Meta label="Version d’index">{m.index_version ?? "—"}</Meta>
            <Meta label="Indexé le">{dateTime(m.indexed_at)}</Meta>
          </dl>
          {m.error && <p className="mt-4 rounded-lg bg-rose-500/10 p-3 text-xs text-rose-200">{m.error}</p>}
        </div>
      </div>

      <section className="card mt-6 p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold text-white">Segments ({m.segments_done}/{m.segments_total})</h2>
          <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
            <Cpu className="size-4 text-accent" aria-hidden />
            {pids.length ? "Couleur = processus qui a calculé le segment :" : "Aucun segment calculé pour l’instant"}
            {pids.map((pid, i) => (
              <span key={pid} className="flex items-center gap-1">
                <span className={`inline-block size-2.5 rounded-sm ${PID_COLORS[i % PID_COLORS.length]}`} /> PID {pid}
              </span>
            ))}
          </div>
        </div>
        <ProgressBar value={m.segments_done} total={m.segments_total || 1} active={m.status === "indexing"} tone="sky" />
        <div className="mt-4 flex h-9 gap-0.5 overflow-hidden rounded-lg" aria-label="Carte des segments">
          {m.segments.map((s) => {
            const color = s.status === "done" ? PID_COLORS[pids.indexOf(s.worker_pid ?? -1) % PID_COLORS.length] : s.status === "failed" ? "bg-rose-500" : "bg-ink-700";
            return (
              <div
                key={s.id}
                className={`${color} min-w-[3px] transition-colors`}
                style={{ flexGrow: s.end_ms - s.start_ms }}
                title={`Segment ${s.segment_index} · ${hms(s.start_ms)}–${hms(s.end_ms)} · ${s.status}${s.worker_pid ? ` · PID ${s.worker_pid}` : ""}${s.processing_ms ? ` · ${duration(s.processing_ms)}` : ""} · ${s.fingerprints_count} empreintes`}
              />
            );
          })}
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-slate-500">
              <tr>
                <th className="py-2 pr-4">#</th>
                <th className="py-2 pr-4">Intervalle</th>
                <th className="py-2 pr-4">Statut</th>
                <th className="py-2 pr-4">Images décodées</th>
                <th className="py-2 pr-4">Empreintes</th>
                <th className="py-2 pr-4">Durée</th>
                <th className="py-2 pr-4">PID</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-700/50 tabular-nums text-slate-300">
              {m.segments.map((s) => (
                <tr key={s.id}>
                  <td className="py-1.5 pr-4">{s.segment_index}</td>
                  <td className="py-1.5 pr-4">{hms(s.start_ms)} → {hms(s.end_ms)}</td>
                  <td className="py-1.5 pr-4"><StatusBadge status={s.status} /></td>
                  <td className="py-1.5 pr-4">{num(s.frames_decoded)}</td>
                  <td className="py-1.5 pr-4">{s.fingerprints_count}</td>
                  <td className="py-1.5 pr-4">{s.processing_ms != null ? duration(s.processing_ms) : "—"}</td>
                  <td className="py-1.5 pr-4">{s.worker_pid ?? "—"}{s.error && <span className="ml-2 text-rose-300" title={s.error}>erreur</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
