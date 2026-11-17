"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowDownAZ, ArrowUpAZ, Film, LayoutGrid, List, RefreshCw, Search, Trash2 } from "lucide-react";
import { api, mediaUrl } from "@/lib/api";
import { bytes, duration, num } from "@/lib/format";
import type { Movie } from "@/lib/types";
import { usePolling } from "@/lib/usePolling";
import { Dropzone } from "@/components/Dropzone";
import { MovieThumb } from "@/components/MovieThumb";
import { EmptyState, ErrorBox, PageHeader, ProgressBar, Skeleton, StatusBadge } from "@/components/ui";

type UploadItem = { id: string; name: string; size: number; progress: number; state: "waiting" | "uploading" | "done" | "error"; message?: string };

export default function Library() {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [sort, setSort] = useState("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [status, setStatus] = useState("");
  const [view, setView] = useState<"grid" | "table">("grid");
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const fetcher = useMemo(() => () => api.movies({ q: debounced, sort, order, status }), [debounced, sort, order, status]);
  const [hasActive, setHasActive] = useState(true);
  const { data: movies, error, loading, refresh } = usePolling(fetcher, hasActive ? 1500 : 8000);
  useEffect(() => {
    setHasActive(!!movies?.some((m) => m.status === "pending" || m.status === "indexing" || m.active_job_id));
  }, [movies]);

  // Envois séquentiels : un fichier à la fois pour ne pas saturer le disque.
  const onFiles = async (files: File[]) => {
    const items: UploadItem[] = files.map((f) => ({ id: `${f.name}-${f.size}-${Math.random()}`, name: f.name, size: f.size, progress: 0, state: "waiting" }));
    setUploads((u) => [...items, ...u]);
    for (const [i, file] of files.entries()) {
      const id = items[i].id;
      const patch = (p: Partial<UploadItem>) => setUploads((u) => u.map((x) => (x.id === id ? { ...x, ...p } : x)));
      patch({ state: "uploading" });
      try {
        const res = await api.importMovie(file, (f) => patch({ progress: f }));
        patch({ state: "done", progress: 1, message: res.message });
        refresh();
      } catch (e) {
        patch({ state: "error", message: e instanceof Error ? e.message : String(e) });
      }
    }
  };

  const remove = async (m: Movie) => {
    if (!confirm(`Supprimer « ${m.title} » ?\n\nLe fichier stocké par MovieShazam, ses empreintes et ses vignettes seront supprimés.`)) return;
    setBusy(m.id);
    try {
      await api.deleteMovie(m.id);
      setNotice(`« ${m.title} » supprimé.`);
      refresh();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const reindex = async (m: Movie, force = false) => {
    setBusy(m.id);
    try {
      const r = await api.reindex(m.id, force);
      setNotice(r.message);
      refresh();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader title="Bibliothèque" subtitle="Importez vos vidéos : elles sont copiées dans le stockage local de l’application, validées par ffprobe puis indexées en parallèle." />

      <Dropzone multiple onFiles={onFiles} title="Glissez-déposez une ou plusieurs vidéos, ou cliquez pour choisir" hint="MP4, MKV, MOV, AVI, WebM… · les films restent sur ce PC" />

      {uploads.length > 0 && (
        <ul className="mt-4 space-y-2">
          {uploads.slice(0, 6).map((u) => (
            <li key={u.id} className="card animate-fade-in p-3">
              <div className="mb-1.5 flex items-center justify-between gap-3 text-sm">
                <span className="truncate text-slate-200">{u.name}</span>
                <span className="shrink-0 text-xs text-slate-500">{bytes(u.size)}</span>
              </div>
              <ProgressBar value={u.progress} total={1} active={u.state === "uploading"} tone={u.state === "error" ? "rose" : u.state === "done" ? "emerald" : "accent"} />
              <p className={`mt-1 text-xs ${u.state === "error" ? "text-rose-300" : "text-slate-500"}`}>
                {u.state === "waiting" && "En attente…"}
                {u.state === "uploading" && (u.progress < 1 ? `Envoi ${Math.round(u.progress * 100)} %` : "Validation ffprobe, empreinte SHA-256…")}
                {(u.state === "done" || u.state === "error") && u.message}
              </p>
            </li>
          ))}
        </ul>
      )}

      {notice && (
        <div className="mt-4 flex items-center justify-between rounded-xl border border-ink-700 bg-ink-850 px-4 py-2 text-sm text-slate-300">
          {notice}
          <button className="text-xs text-slate-500 hover:text-white" onClick={() => setNotice(null)}>
            Fermer
          </button>
        </div>
      )}

      <div className="mt-6 flex flex-col gap-3 md:flex-row md:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-500" aria-hidden />
          <input className="input pl-9" placeholder="Rechercher un titre ou un nom de fichier…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Recherche textuelle" />
        </div>
        <div className="flex flex-wrap gap-2">
          <select className="input w-auto" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filtrer par statut">
            <option value="">Tous les statuts</option>
            <option value="indexed">Indexés</option>
            <option value="indexing">En indexation</option>
            <option value="pending">En attente</option>
            <option value="failed">En échec</option>
            <option value="cancelled">Annulés</option>
          </select>
          <select className="input w-auto" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Trier par">
            <option value="created_at">Date d’ajout</option>
            <option value="title">Titre</option>
            <option value="year">Année</option>
            <option value="duration">Durée</option>
            <option value="size">Taille</option>
          </select>
          <button className="btn-ghost" onClick={() => setOrder(order === "asc" ? "desc" : "asc")} aria-label="Inverser l'ordre">
            {order === "asc" ? <ArrowUpAZ className="size-4" /> : <ArrowDownAZ className="size-4" />}
          </button>
          <button className="btn-ghost" onClick={() => setView(view === "grid" ? "table" : "grid")} aria-label="Changer de vue">
            {view === "grid" ? <List className="size-4" /> : <LayoutGrid className="size-4" />}
          </button>
        </div>
      </div>

      <div className="mt-4">
        {error && <ErrorBox message={error} onRetry={refresh} />}
        {loading && !movies && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-64" />)}
          </div>
        )}
        {movies && movies.length === 0 && (
          <EmptyState icon={<Film className="size-5" />} title={debounced || status ? "Aucun film ne correspond" : "Bibliothèque vide"}>
            {debounced || status ? "Modifiez la recherche ou le filtre." : "Importez des vidéos (par exemple le corpus de démonstration généré dans data/demo/movies) pour commencer."}
          </EmptyState>
        )}
        {movies && movies.length > 0 && view === "grid" && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {movies.map((m) => (
              <article key={m.id} className="card animate-fade-in overflow-hidden">
                <Link href={`/bibliotheque/${m.id}`} className="block">
                  <MovieThumb src={m.has_thumbnail ? mediaUrl.thumbnail(m.id) : null} alt={m.title} className="aspect-video w-full" />
                </Link>
                <div className="space-y-3 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <Link href={`/bibliotheque/${m.id}`} className="block truncate font-semibold text-white hover:text-accent">
                        {m.title}
                      </Link>
                      <p className="text-xs text-slate-500">
                        {[m.year, duration(m.duration_ms), `${m.width}×${m.height}`, bytes(m.file_size)].filter(Boolean).join(" · ")}
                      </p>
                    </div>
                    <StatusBadge status={m.status} />
                  </div>
                  {(m.status === "indexing" || m.status === "pending" || m.active_job_id) && (
                    <div>
                      <ProgressBar value={m.segments_done} total={m.segments_total || 1} active={m.status === "indexing"} tone="sky" />
                      <p className="mt-1 text-xs tabular-nums text-slate-500">
                        {m.segments_done}/{m.segments_total || "?"} segments · {num(m.fingerprints_count)} empreintes
                      </p>
                    </div>
                  )}
                  {m.status === "indexed" && <p className="text-xs text-slate-500">{num(m.fingerprints_count)} empreintes · {m.segments_total} segments</p>}
                  {m.error && <p className="line-clamp-2 text-xs text-rose-300" title={m.error}>{m.error}</p>}
                  <div className="flex gap-2">
                    <button className="btn-ghost flex-1 px-3 py-1.5 text-xs" disabled={busy === m.id || !!m.active_job_id} onClick={() => reindex(m)} title="Indexation incrémentale : ne recalcule que ce qui manque">
                      <RefreshCw className="size-3.5" /> Réindexer
                    </button>
                    <button className="btn-danger px-3 py-1.5 text-xs" disabled={busy === m.id} onClick={() => remove(m)} aria-label={`Supprimer ${m.title}`}>
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
        {movies && movies.length > 0 && view === "table" && (
          <div className="card overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-ink-700 text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="p-3">Titre</th>
                  <th className="p-3">Durée</th>
                  <th className="p-3">Résolution</th>
                  <th className="p-3">Format</th>
                  <th className="p-3">Taille</th>
                  <th className="p-3">Empreintes</th>
                  <th className="p-3">Statut</th>
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-700/60">
                {movies.map((m) => (
                  <tr key={m.id} className="hover:bg-ink-850">
                    <td className="p-3">
                      <Link href={`/bibliotheque/${m.id}`} className="font-medium text-slate-100 hover:text-accent">
                        {m.title}
                      </Link>
                      {m.year && <span className="ml-2 text-xs text-slate-500">{m.year}</span>}
                    </td>
                    <td className="p-3 tabular-nums text-slate-300">{duration(m.duration_ms)}</td>
                    <td className="p-3 text-slate-300">{m.width}×{m.height}</td>
                    <td className="p-3 text-slate-400">{m.codec} · {m.format_name.split(",")[0]}</td>
                    <td className="p-3 tabular-nums text-slate-300">{bytes(m.file_size)}</td>
                    <td className="p-3 tabular-nums text-slate-300">{num(m.fingerprints_count)}</td>
                    <td className="p-3"><StatusBadge status={m.status} /></td>
                    <td className="p-3 text-right">
                      <button className="btn-danger px-2 py-1 text-xs" disabled={busy === m.id} onClick={() => remove(m)} aria-label={`Supprimer ${m.title}`}>
                        <Trash2 className="size-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
