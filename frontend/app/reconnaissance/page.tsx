"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, Film, Loader2, ScanSearch, SearchX, X } from "lucide-react";
import { api, mediaUrl } from "@/lib/api";
import { bytes, duration, hms, num, pct, relative } from "@/lib/format";
import type { Health, Hypothesis, Movie, Search } from "@/lib/types";
import { Dropzone } from "@/components/Dropzone";
import { MovieThumb } from "@/components/MovieThumb";
import { ScoreBreakdown, ScoreGauge } from "@/components/ScoreBreakdown";
import { Timeline } from "@/components/Timeline";
import { ErrorBox, PageHeader, ProgressBar, StatusBadge } from "@/components/ui";

const STEPS = [
  { key: "validation_ms", label: "Validation du clip", help: "ffprobe : flux vidéo, durée autorisée" },
  { key: "fingerprint_ms", label: "Empreintes du clip", help: "décodage FFmpeg en flux + pHash 64 bits" },
  { key: "candidates_ms", label: "Recherche des candidats", help: "index par bandes SQLite + distance de Hamming" },
  { key: "alignment_ms", label: "Alignement temporel", help: "vote sur les décalages, ordre et cohérence" },
  { key: "total_ms", label: "Résultat", help: "décision : trouvé / ambigu / aucun résultat fiable" },
];

function RecognitionInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [clipDuration, setClipDuration] = useState<number | null>(null);
  const [method, setMethod] = useState<"bands" | "exhaustive">("bands");
  const [uploadProgress, setUploadProgress] = useState(0);
  const [phase, setPhase] = useState<"idle" | "uploading" | "processing" | "done">("idle");
  const [search, setSearch] = useState<Search | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [history, setHistory] = useState<Search[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
    api.searches(8).then(setHistory).catch(() => {});
  }, []);

  const poll = useCallback((id: string) => {
    const tick = async () => {
      try {
        const s = await api.search(id);
        setSearch(s);
        if (s.status === "queued" || s.status === "running") {
          timer.current = setTimeout(tick, 800);
        } else {
          setPhase("done");
          api.searches(8).then(setHistory).catch(() => {});
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setPhase("idle");
      }
    };
    tick();
  }, []);

  useEffect(() => {
    const id = params.get("search");
    if (id && search?.id !== id) {
      setPhase("processing");
      poll(id);
    }
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const choose = (files: File[]) => {
    const f = files[0];
    if (preview) URL.revokeObjectURL(preview);
    setFile(f);
    setPreview(URL.createObjectURL(f)); // prévisualisation locale, rien n'est envoyé avant le clic
    setClipDuration(null);
    setSearch(null);
    setError(null);
    setPhase("idle");
  };

  const reset = () => {
    if (preview) URL.revokeObjectURL(preview);
    setFile(null);
    setPreview(null);
    setSearch(null);
    setError(null);
    setPhase("idle");
    router.replace("/reconnaissance");
  };

  const identify = async () => {
    if (!file) return;
    setError(null);
    setSearch(null);
    setPhase("uploading");
    setUploadProgress(0);
    try {
      const { search_id } = await api.startSearch(file, method, setUploadProgress);
      setPhase("processing");
      router.replace(`/reconnaissance?search=${search_id}`);
      poll(search_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("idle");
    }
  };

  const [lo, hi] = health?.limits.query_duration_s ?? [4, 30];
  const outOfRange = clipDuration != null && (clipDuration < lo || clipDuration > hi + 0.5);
  const result = search?.result;
  const final = search && !["queued", "running"].includes(search.status);

  return (
    <>
      <PageHeader title="Reconnaissance" subtitle="Déposez un extrait de quelques secondes : MovieShazam calcule ses empreintes visuelles et cherche le film et l’instant correspondants dans la bibliothèque indexée." />

      <div className="grid gap-6 lg:grid-cols-5">
        <section className="space-y-4 lg:col-span-2">
          {!preview ? (
            <Dropzone onFiles={choose} title="Glissez-déposez un clip vidéo (5 à 30 s)" hint="ou cliquez pour choisir un fichier" />
          ) : (
            <div className="card overflow-hidden">
              <div className="relative">
                <video src={preview} controls muted className="aspect-video w-full bg-black" onLoadedMetadata={(e) => setClipDuration(e.currentTarget.duration)} />
                {phase === "idle" && (
                  <button onClick={reset} className="absolute right-2 top-2 rounded-full bg-black/60 p-1.5 text-white hover:bg-black" aria-label="Retirer le clip">
                    <X className="size-4" />
                  </button>
                )}
              </div>
              <div className="flex items-center justify-between gap-3 p-3 text-xs text-slate-400">
                <span className="truncate text-slate-200">{file?.name ?? search?.clip_name}</span>
                <span className="shrink-0 tabular-nums">
                  {clipDuration != null && `${clipDuration.toFixed(1)} s · `}
                  {file && bytes(file.size)}
                </span>
              </div>
              {outOfRange && (
                <p className="mx-3 mb-3 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-200">
                  Durée hors de la fenêtre autorisée ({lo}–{hi} s) : le backend refusera ce clip.
                </p>
              )}
            </div>
          )}

          {file && (
            <div className="card space-y-4 p-4">
              <fieldset>
                <legend className="label mb-2">Méthode de recherche des candidats</legend>
                <div className="grid grid-cols-2 gap-2">
                  {(
                    [
                      ["bands", "Index par bandes", "production : rapide sur grande bibliothèque"],
                      ["exhaustive", "Exhaustive", "référence : compare à toutes les empreintes"],
                    ] as const
                  ).map(([value, title, sub]) => (
                    <label key={value} className={`cursor-pointer rounded-xl border p-3 text-sm transition ${method === value ? "border-accent/70 bg-accent/5" : "border-ink-700 hover:border-ink-600"}`}>
                      <input type="radio" name="method" value={value} checked={method === value} onChange={() => setMethod(value)} className="sr-only" />
                      <span className="block font-medium text-slate-100">{title}</span>
                      <span className="block text-xs text-slate-400">{sub}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <button className="btn-primary w-full py-2.5" disabled={phase === "uploading" || phase === "processing"} onClick={identify}>
                {phase === "uploading" || phase === "processing" ? <Loader2 className="size-4 animate-spin" /> : <ScanSearch className="size-4" />}
                Identifier le film
              </button>
              {phase === "uploading" && <ProgressBar value={uploadProgress} total={1} active />}
            </div>
          )}

          {(phase === "processing" || phase === "done") && search && <Steps search={search} />}
          {error && <ErrorBox message={error} />}
        </section>

        <section className="lg:col-span-3">
          {!search && phase !== "processing" && (
            <div className="card flex h-full min-h-72 flex-col items-center justify-center gap-3 p-8 text-center">
              <div className="grid size-14 place-items-center rounded-2xl bg-ink-800 text-accent">
                <ScanSearch className="size-6" />
              </div>
              <h2 className="text-lg font-semibold text-white">Quel film, à quel instant ?</h2>
              <p className="max-w-md text-sm text-slate-400">
                Le résultat indiquera le film, le début et la fin estimés du passage, un score de similarité expliqué, la couverture temporelle et les meilleurs candidats.
              </p>
            </div>
          )}
          {phase === "processing" && !final && (
            <div className="card flex min-h-72 flex-col items-center justify-center gap-3 p-8 text-center">
              <Loader2 className="size-8 animate-spin text-accent" />
              <p className="text-sm text-slate-300">{search?.job?.stage ?? "Envoi du clip…"}</p>
            </div>
          )}
          {final && search && result && <ResultView search={search} />}
        </section>
      </div>

      {history.length > 0 && (
        <section className="card mt-8 p-5">
          <h2 className="mb-3 font-semibold text-white">Historique des recherches</h2>
          <ul className="divide-y divide-ink-700/60">
            {history.map((s) => (
              <li key={s.id}>
                <Link href={`/reconnaissance?search=${s.id}`} className="flex items-center justify-between gap-3 py-2 text-sm hover:text-accent">
                  <span className="min-w-0 truncate">
                    <span className="text-slate-200">{s.clip_name}</span>
                    <span className="ml-2 text-xs text-slate-500">
                      {s.result.best ? `${s.result.best.title} · ${s.result.best.start_hms}` : ""} · {relative(s.created_at)}
                    </span>
                  </span>
                  <StatusBadge status={s.status} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function Steps({ search }: { search: Search }) {
  const done = search.job?.completed_units ?? 0;
  const final = !["queued", "running"].includes(search.status);
  const failed = search.status === "failed" || search.status === "cancelled";
  const timings = search.result.timings ?? {};
  return (
    <ol className="card space-y-3 p-4" aria-label="Étapes de la recherche">
      {STEPS.map((s, i) => {
        const state = final && !failed ? "done" : i < done ? "done" : i === done && !final ? "active" : failed && i === done ? "failed" : "todo";
        return (
          <li key={s.key} className="flex items-start gap-3">
            <span className="mt-0.5">
              {state === "done" && <CheckCircle2 className="size-4 text-emerald-400" />}
              {state === "active" && <Loader2 className="size-4 animate-spin text-accent" />}
              {state === "failed" && <X className="size-4 text-rose-400" />}
              {state === "todo" && <CircleDashed className="size-4 text-slate-600" />}
            </span>
            <span className="flex-1">
              <span className={`block text-sm ${state === "todo" ? "text-slate-500" : "text-slate-100"}`}>{s.label}</span>
              <span className="block text-xs text-slate-500">{s.help}</span>
            </span>
            {timings[s.key] != null && <span className="text-xs tabular-nums text-slate-400">{duration(timings[s.key])}</span>}
          </li>
        );
      })}
    </ol>
  );
}

function ResultView({ search }: { search: Search }) {
  const r = search.result;
  const maxH = r.params?.max_hamming ?? 12;
  if (search.status === "failed" || search.status === "cancelled") {
    return <ErrorBox message={`Recherche ${search.status === "failed" ? "en échec" : "annulée"} : ${r.error ?? "erreur inconnue"}`} />;
  }
  if (search.status === "match" && r.best) return <MatchView search={search} best={r.best} maxH={maxH} />;
  const ambiguous = search.status === "ambiguous";
  return (
    <div className="space-y-4">
      <div className={`card p-6 ${ambiguous ? "border-amber-500/40" : ""}`}>
        <div className="flex items-start gap-4">
          <div className={`grid size-12 shrink-0 place-items-center rounded-2xl ${ambiguous ? "bg-amber-500/15 text-amber-300" : "bg-ink-800 text-slate-300"}`}>
            {ambiguous ? <AlertTriangle className="size-6" /> : <SearchX className="size-6" />}
          </div>
          <div>
            <h2 className="text-xl font-semibold text-white">
              {ambiguous ? "Résultat ambigu" : search.status === "insufficient" ? "Clip trop peu informatif" : "Aucune correspondance suffisamment fiable"}
            </h2>
            <p className="mt-1 text-sm text-slate-400">{r.reason}</p>
            {ambiguous && <p className="mt-2 text-sm text-slate-300">Le même passage existe à plusieurs endroits : MovieShazam préfère le signaler plutôt que d’inventer un gagnant.</p>}
          </div>
        </div>
      </div>
      {(r.candidates?.length ?? 0) > 0 && (
        <div className="card p-5">
          <h3 className="mb-3 font-semibold text-white">{ambiguous ? "Placements possibles" : "Meilleurs candidats (rejetés)"}</h3>
          <div className="space-y-5">
            {r.candidates!.map((c, i) => (
              <div key={i}>
                <div className="mb-2 flex items-center justify-between gap-3 text-sm">
                  <Link href={`/bibliotheque/${c.movie_id}`} className="truncate font-medium text-slate-100 hover:text-accent">
                    {i + 1}. {c.title}
                  </Link>
                  <span className="shrink-0 tabular-nums text-slate-400">
                    {c.start_hms} · score {c.score.toFixed(2)} · couverture {pct(c.coverage)}
                  </span>
                </div>
                {c.movie_duration_ms && <Timeline durationMs={c.movie_duration_ms} startMs={c.start_ms} endMs={c.end_ms} tone="amber" />}
              </div>
            ))}
          </div>
        </div>
      )}
      <ClipStats search={search} />
    </div>
  );
}

function MatchView({ search, best, maxH }: { search: Search; best: Hypothesis; maxH: number }) {
  const r = search.result;
  const [movie, setMovie] = useState<Movie | null>(null);
  useEffect(() => {
    api.movie(best.movie_id).then(setMovie).catch(() => setMovie(null));
  }, [best.movie_id]);
  const mid = (best.start_ms + best.end_ms) / 2;
  return (
    <div className="space-y-4 animate-fade-in">
      <div className="card overflow-hidden border-emerald-500/30">
        <div className="grid gap-0 sm:grid-cols-5">
          <MovieThumb src={mediaUrl.frame(best.movie_id, best.start_ms + 500)} alt={`Image du film à ${best.start_hms}`} className="aspect-video h-full w-full sm:col-span-2" />
          <div className="p-5 sm:col-span-3">
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-emerald-300">
              <CheckCircle2 className="size-4" /> Film identifié
            </div>
            <h2 className="mt-1 text-2xl font-semibold text-white">
              {best.title} {best.year && <span className="text-base font-normal text-slate-400">({best.year})</span>}
            </h2>
            <p className="mt-2 text-lg tabular-nums text-slate-200">
              de <strong className="text-accent">{best.start_hms}</strong> à <strong className="text-accent">{best.end_hms}</strong>
            </p>
            <p className="text-xs text-slate-500">début estimé : {hms(best.start_ms, true)} (précision au pas d’échantillonnage du clip : {r.clip?.sample_step_ms ?? 100} ms)</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link href={`/bibliotheque/${best.movie_id}`} className="btn-ghost px-3 py-1.5 text-xs">
                <Film className="size-3.5" /> Fiche du film
              </Link>
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-5">
        <div className="card p-5 md:col-span-2">
          <ScoreGauge score={best.score} threshold={r.params?.accept_score} margin={r.margin} />
        </div>
        <div className="card p-5 md:col-span-3">
          <ScoreBreakdown h={best} margin={r.margin} maxHamming={maxH} />
        </div>
      </div>

      <div className="card p-5">
        <h3 className="mb-3 font-semibold text-white">Position dans le film</h3>
        <Timeline durationMs={best.movie_duration_ms ?? best.end_ms} startMs={best.start_ms} endMs={best.end_ms} aligned={best.aligned} maxHamming={maxH} />
        <div className="mt-4 grid grid-cols-3 gap-2">
          {[best.start_ms + 500, mid, best.end_ms - 500].map((t) => (
            <figure key={t}>
              <MovieThumb src={mediaUrl.frame(best.movie_id, t)} alt={`Film à ${hms(t)}`} className="aspect-video w-full rounded-lg" />
              <figcaption className="mt-1 text-center text-[11px] tabular-nums text-slate-500">{hms(t, true)}</figcaption>
            </figure>
          ))}
        </div>
        {movie?.playable_in_browser && (
          <video key={best.start_ms} src={`${mediaUrl.video(best.movie_id)}#t=${(best.start_ms / 1000).toFixed(1)},${(best.end_ms / 1000).toFixed(1)}`} controls preload="metadata" className="mt-4 aspect-video w-full rounded-xl bg-black" />
        )}
      </div>

      {(r.candidates?.length ?? 0) > 1 && (
        <div className="card p-5">
          <h3 className="mb-3 font-semibold text-white">Top {r.candidates!.length} des placements</h3>
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-slate-500">
              <tr>
                <th className="py-1.5">Film</th>
                <th className="py-1.5">Début</th>
                <th className="py-1.5 text-right">Score</th>
                <th className="py-1.5 text-right">Couverture</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-700/60 tabular-nums">
              {r.candidates!.map((c, i) => (
                <tr key={i} className={i === 0 ? "text-slate-100" : "text-slate-400"}>
                  <td className="py-1.5">{c.title}</td>
                  <td className="py-1.5">{c.start_hms}</td>
                  <td className="py-1.5 text-right">{c.score.toFixed(3)}</td>
                  <td className="py-1.5 text-right">{pct(c.coverage)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ClipStats search={search} />
    </div>
  );
}

function ClipStats({ search }: { search: Search }) {
  const r = search.result;
  if (!r.clip) return null;
  return (
    <details className="card p-4 text-xs text-slate-400">
      <summary className="cursor-pointer text-sm text-slate-300">Détails techniques</summary>
      <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <div>Clip : {r.clip.width}×{r.clip.height}, {r.clip.codec}, {r.clip.fps} img/s</div>
        <div>Durée : {duration(r.clip.duration_ms)}</div>
        <div>Empreintes du clip : {r.clip.frames} (pas {r.clip.sample_step_ms} ms)</div>
        <div>Images informatives : {r.informative_frames}/{r.total_frames}</div>
        <div>Méthode : {r.method === "exhaustive" ? "exhaustive (référence)" : "index par bandes"}</div>
        {r.candidate_stats && <div>Lignes d’index lues : {num(r.candidate_stats.rows_scanned)}</div>}
        {r.candidate_stats && <div>Candidats (d ≤ {r.params?.max_hamming}) : {num(r.candidate_stats.candidates)}</div>}
        {r.timings && <div>Temps total : {duration(r.timings.total_ms)}</div>}
        {r.params && <div>Seuil d’acceptation : score ≥ {r.params.accept_score}, couverture ≥ {pct(r.params.min_coverage)}</div>}
      </dl>
    </details>
  );
}

export default function RecognitionPage() {
  return (
    <Suspense fallback={<div className="card h-72 animate-pulse" />}>
      <RecognitionInner />
    </Suspense>
  );
}
