"use client";

import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Cpu, Download, FlaskConical, Gauge, Info, Loader2, Play } from "lucide-react";
import { api, mediaUrl } from "@/lib/api";
import { dateTime, duration, num } from "@/lib/format";
import type { BenchRun, Job } from "@/lib/types";
import { usePolling } from "@/lib/usePolling";
import { EmptyState, ErrorBox, PageHeader, ProgressBar, StatusBadge } from "@/components/ui";

const AXIS = { stroke: "#64748b", fontSize: 12 };
const GRID = "#243041";
const TIP = { contentStyle: { background: "#0d1117", border: "1px solid #334155", borderRadius: 12, fontSize: 12 }, labelStyle: { color: "#e2e8f0" } };

export default function PerformancePage() {
  const { data, error, refresh } = usePolling(api.benchmarks, 60_000);
  const [selected, setSelected] = useState<string | null>(null);
  const [modes, setModes] = useState<number[]>([0, 1, 2, 4]);
  const [repeats, setRepeats] = useState(1);
  const [job, setJob] = useState<Job | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);

  const cpus = data?.machine.logical_cpus ?? 4;
  const runs = data?.runs ?? [];
  const run: BenchRun | undefined = runs.find((r) => r.run_id === selected) ?? runs.find((r) => r.index.length > 0);
  // Les mesures de recherche (rappel, passage à l'échelle) sont rattachées à une seule exécution :
  // on affiche celles de l'exécution choisie, sinon les plus récentes disponibles.
  const recallRun = run?.recall?.available ? run : runs.find((r) => r.recall?.available);

  useEffect(() => {
    if (!job || job.state === "completed" || job.state === "failed" || job.state === "cancelled") return;
    const t = setTimeout(async () => {
      const j = await api.job(job.id);
      setJob(j);
      if (j.state === "completed") {
        await refresh();
        setSelected(null);
      }
    }, 1000);
    return () => clearTimeout(t);
  }, [job, refresh]);

  const launch = async () => {
    setLaunchError(null);
    try {
      setJob(await api.runBenchmark({ modes, repeats, include_recall: true }));
    } catch (e) {
      setLaunchError(e instanceof Error ? e.message : String(e));
    }
  };

  const chart = useMemo(
    () =>
      run?.index.map((x) => ({
        name: x.mode === "sequential" ? "Séquentiel" : `${x.process_count} proc.`,
        temps: +(x.elapsed_ms_median / 1000).toFixed(2),
        speedup: x.speedup,
        ideal: x.mode === "sequential" ? 1 : x.process_count,
        efficacite: x.efficiency,
        images: x.frames_per_s,
        ram: x.peak_ram_mb,
        coeurs: +x.busy_cores_avg.toFixed(2),
      })) ?? [],
    [run],
  );
  const recall = useMemo(() => {
    const curve = recallRun?.recall?.curve ?? [];
    const ds = [...new Set(curve.map((c) => c.distance))].sort((a, b) => a - b);
    return ds.map((d) => ({
      d,
      sans: curve.find((c) => c.distance === d && c.probe_radius === 0)?.recall,
      avec: curve.find((c) => c.distance === d && c.probe_radius === 1)?.recall,
    }));
  }, [recallRun]);
  const running = job && (job.state === "queued" || job.state === "running");

  return (
    <>
      <PageHeader
        title="Performances"
        subtitle="Mesures réelles du parallélisme local : même corpus, mêmes tâches et même fonction d’analyse en séquentiel et avec N processus. Aucune valeur n’est simulée."
        actions={
          run && (
            <>
              <a className="btn-ghost" href={mediaUrl.export("csv", run.run_id)}>
                <Download className="size-4" /> CSV
              </a>
              <a className="btn-ghost" href={mediaUrl.export("json", run.run_id)}>
                <Download className="size-4" /> JSON
              </a>
            </>
          )
        }
      />
      {error && <ErrorBox message={error} onRetry={refresh} />}

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="card p-5 lg:col-span-1">
          <h2 className="mb-4 flex items-center gap-2 font-semibold text-white">
            <FlaskConical className="size-4 text-accent" /> Lancer un benchmark
          </h2>
          <fieldset className="space-y-2">
            <legend className="label mb-2">Modes à comparer</legend>
            {[0, 1, 2, 4].map((m) => (
              <label key={m} className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm ${m > cpus ? "opacity-40" : "hover:bg-ink-850"}`}>
                <input
                  type="checkbox"
                  className="size-4 accent-[#f5b544]"
                  disabled={m === 0 || m > cpus}
                  checked={modes.includes(m)}
                  onChange={(e) => setModes((v) => (e.target.checked ? [...v, m] : v.filter((x) => x !== m)).sort())}
                />
                {m === 0 ? "Séquentiel (référence, obligatoire)" : `${m} processus`}
                {m > cpus && <span className="text-xs text-slate-500">(&gt; {cpus} cœurs)</span>}
              </label>
            ))}
          </fieldset>
          <label className="mt-4 block">
            <span className="label">Répétitions (médiane)</span>
            <select className="input mt-1" value={repeats} onChange={(e) => setRepeats(Number(e.target.value))}>
              {[1, 2, 3].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <button className="btn-primary mt-4 w-full" disabled={!!running} onClick={launch}>
            {running ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />} Lancer sur les films indexés
          </button>
          <p className="mt-2 text-xs text-slate-500">Le benchmark attend la fin des indexations en cours (exclusivité CPU) et écrit dans une base jetable : l’index réel n’est pas modifié.</p>
          {launchError && <div className="mt-3"><ErrorBox message={launchError} /></div>}
          {job && (
            <div className="mt-4 space-y-2 rounded-xl bg-ink-850 p-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-300">{job.stage}</span>
                <StatusBadge status={job.state} />
              </div>
              <ProgressBar value={job.completed_units} total={job.total_units || 1} active={!!running} />
              {job.worker_pids.length > 0 && <p className="text-xs text-slate-500">PID observés : {job.worker_pids.join(", ")}</p>}
              {job.error && <p className="text-xs text-rose-300">{job.error}</p>}
            </div>
          )}
        </section>

        <section className="card p-5 lg:col-span-2">
          <h2 className="mb-4 flex items-center gap-2 font-semibold text-white">
            <Cpu className="size-4 text-accent" /> Machine de mesure
          </h2>
          {data?.machine ? (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
              <div className="col-span-2 sm:col-span-3"><dt className="text-xs text-slate-500">Processeur</dt><dd className="text-slate-100">{data.machine.cpu}</dd></div>
              <div><dt className="text-xs text-slate-500">Cœurs physiques / logiques</dt><dd className="text-slate-100">{data.machine.physical_cores} / {data.machine.logical_cpus}</dd></div>
              <div><dt className="text-xs text-slate-500">RAM totale (libre)</dt><dd className="text-slate-100">{data.machine.ram_total_gb} Go ({data.machine.ram_available_gb} Go)</dd></div>
              <div><dt className="text-xs text-slate-500">Système · Python</dt><dd className="text-slate-100">{data.machine.os} · {data.machine.python}</dd></div>
              <div className="col-span-2"><dt className="text-xs text-slate-500">FFmpeg</dt><dd className="truncate text-slate-100">{data.machine.ffmpeg.split(" ").slice(0, 3).join(" ")}</dd></div>
              <div><dt className="text-xs text-slate-500">Moteur</dt><dd className="text-slate-100">{String(data.machine.engine.index_version)} · FFmpeg {String(data.machine.engine.ffmpeg_threads_per_process)} thread/proc.</dd></div>
            </dl>
          ) : (
            <p className="text-sm text-slate-500">Chargement…</p>
          )}
          {runs.length > 0 && (
            <label className="mt-5 block">
              <span className="label">Exécution affichée</span>
              <select className="input mt-1" value={run?.run_id ?? ""} onChange={(e) => setSelected(e.target.value)}>
                {runs
                  .filter((r) => r.index.length)
                  .map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      {dateTime(r.created_at)} — {r.corpus?.map((c) => c.label).join(", ").slice(0, 70) ?? r.dataset_id} ({r.segments} segments)
                    </option>
                  ))}
              </select>
            </label>
          )}
        </section>
      </div>

      {!run && data && (
        <div className="mt-6">
          <EmptyState icon={<Gauge className="size-5" />} title="Aucun benchmark enregistré">
            Indexez au moins un film puis lancez un benchmark ici, ou exécutez <code className="text-slate-300">python scripts/run_benchmark.py</code>.
          </EmptyState>
        </div>
      )}

      {run && (
        <>
          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <ChartCard title="Temps d’indexation (s, médiane)">
              <BarChart data={chart}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="name" {...AXIS} />
                <YAxis {...AXIS} />
                <Tooltip {...TIP} cursor={{ fill: "#18202c" }} />
                <Bar dataKey="temps" name="temps (s)" fill="#f5b544" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ChartCard>
            <ChartCard title="Speedup mesuré vs idéal">
              <LineChart data={chart}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="name" {...AXIS} />
                <YAxis {...AXIS} />
                <Tooltip {...TIP} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line dataKey="ideal" name="idéal (N)" stroke="#64748b" strokeDasharray="5 5" dot={false} />
                <Line dataKey="speedup" name="speedup mesuré" stroke="#f5b544" strokeWidth={2.5} />
              </LineChart>
            </ChartCard>
            <ChartCard title="Débit (images décodées / s)">
              <BarChart data={chart}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="name" {...AXIS} />
                <YAxis {...AXIS} />
                <Tooltip {...TIP} cursor={{ fill: "#18202c" }} />
                <Bar dataKey="images" name="images/s" fill="#38bdf8" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ChartCard>
            <ChartCard title="Mémoire de pointe (Mo, processus + FFmpeg)">
              <BarChart data={chart}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="name" {...AXIS} />
                <YAxis {...AXIS} />
                <Tooltip {...TIP} cursor={{ fill: "#18202c" }} />
                <Bar dataKey="ram" name="RAM (Mo)" fill="#a78bfa" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ChartCard>
          </div>

          <section className="card mt-6 overflow-x-auto p-5">
            <h2 className="mb-3 font-semibold text-white">
              Détail — {run.corpus?.length ?? "?"} film(s), {duration(run.corpus?.reduce((a, c) => a + c.duration_ms, 0) ?? 0)} de vidéo, {run.segments} segments
            </h2>
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="py-2 pr-3">Mode</th>
                  <th className="py-2 pr-3 text-right">Temps</th>
                  <th className="py-2 pr-3 text-right">Speedup</th>
                  <th className="py-2 pr-3 text-right">Efficacité</th>
                  <th className="py-2 pr-3 text-right">Images/s</th>
                  <th className="py-2 pr-3 text-right">Empreintes/s</th>
                  <th className="py-2 pr-3 text-right">Écriture SQLite</th>
                  <th className="py-2 pr-3 text-right">Cœurs occupés</th>
                  <th className="py-2 pr-3 text-right">RAM pic</th>
                  <th className="py-2 pr-3">Empreintes identiques</th>
                  <th className="py-2">PID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-700/60 tabular-nums text-slate-300">
                {run.index.map((x) => (
                  <tr key={`${x.mode}-${x.process_count}`}>
                    <td className="whitespace-nowrap py-2 pr-3 text-slate-100">{x.mode === "sequential" ? "Séquentiel" : `${x.process_count} processus`}</td>
                    <td className="py-2 pr-3 text-right">{duration(x.elapsed_ms_median)}</td>
                    <td className="py-2 pr-3 text-right font-medium text-accent">×{x.speedup.toFixed(2)}</td>
                    <td className="py-2 pr-3 text-right">{(x.efficiency * 100).toFixed(0)} %</td>
                    <td className="py-2 pr-3 text-right">{num(x.frames_per_s)}</td>
                    <td className="py-2 pr-3 text-right">{num(x.fingerprints_per_s, 1)}</td>
                    <td className="py-2 pr-3 text-right">{duration(x.write_ms_median)}</td>
                    <td className="py-2 pr-3 text-right">{x.busy_cores_avg.toFixed(1)}</td>
                    <td className="py-2 pr-3 text-right">{num(x.peak_ram_mb)} Mo</td>
                    <td className="py-2 pr-3">{x.identical_to_sequential ? <span className="text-emerald-300">oui ✓</span> : <span className="text-rose-300">non</span>}</td>
                    <td className="py-2 text-xs text-slate-500">{x.distinct_pids.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {recallRun?.recall?.available && (
            <div className="mt-6 grid gap-6 lg:grid-cols-2">
              <ChartCard title="Rappel de l’index par bandes selon la distance de Hamming">
                <LineChart data={recall}>
                  <CartesianGrid stroke={GRID} vertical={false} />
                  <XAxis dataKey="d" {...AXIS} label={{ value: "bits modifiés", position: "insideBottom", offset: -2, fill: "#64748b", fontSize: 11 }} />
                  <YAxis domain={[0, 1]} {...AXIS} />
                  <Tooltip {...TIP} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Line dataKey="sans" name="sans multi-probe" stroke="#64748b" strokeWidth={2} />
                  <Line dataKey="avec" name="multi-probe 1 bit" stroke="#34d399" strokeWidth={2.5} />
                </LineChart>
              </ChartCard>
              {recallRun.recall.scaling ? (
                <ChartCard title="Temps de recherche selon la taille de l’index (ms, médiane)">
                  <LineChart data={recallRun.recall.scaling.map((s) => ({ n: num(s.index_fingerprints), bandes: s.bands_ms_median, exhaustive: s.exhaustive_ms_median }))}>
                    <CartesianGrid stroke={GRID} vertical={false} />
                    <XAxis dataKey="n" {...AXIS} />
                    <YAxis {...AXIS} />
                    <Tooltip {...TIP} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Line dataKey="exhaustive" name="exhaustive (référence)" stroke="#f87171" strokeWidth={2} />
                    <Line dataKey="bandes" name="index par bandes" stroke="#34d399" strokeWidth={2.5} />
                  </LineChart>
                </ChartCard>
              ) : (
                <div className="card p-5 text-sm text-slate-400">
                  <h3 className="mb-2 font-semibold text-white">Index par bandes vs exhaustive</h3>
                  <p>
                    {recallRun.recall.samples} empreintes réelles testées sur un index de {num(recallRun.recall.index_size)} empreintes. Recherche exhaustive des mêmes requêtes : {duration(recallRun.recall.exhaustive_ms_same_queries)}.
                  </p>
                  <p className="mt-2">La comparaison de passage à l’échelle (index de plusieurs centaines de milliers d’empreintes) est produite par <code className="text-slate-300">scripts/run_benchmark.py</code>.</p>
                </div>
              )}
            </div>
          )}

          <section className="card mt-6 p-5 text-sm leading-relaxed text-slate-300">
            <h2 className="mb-2 flex items-center gap-2 font-semibold text-white">
              <Info className="size-4 text-accent" /> Lire ces résultats
            </h2>
            <ul className="list-disc space-y-1.5 pl-5 text-slate-400">
              <li><strong className="text-slate-200">speedup(N) = T_séquentiel / T_N</strong> et <strong className="text-slate-200">efficacité = speedup / N</strong>. Un speedup &lt; N est normal : démarrage des processus, sérialisation des résultats, écriture SQLite séquentielle (loi d’Amdahl).</li>
              <li>Le décodage vidéo HD lit et écrit beaucoup de mémoire : sur une machine à <strong className="text-slate-200">un seul canal mémoire</strong>, plusieurs décodeurs se disputent la bande passante et 4 processus peuvent être plus lents que 2. Sur une vidéo basse résolution, le travail est limité par le CPU et le gain est bien plus net.</li>
              <li>Toutes les mesures sont « à chaud » (fichiers déjà dans le cache disque de l’OS) ; la colonne « empreintes identiques » vérifie que le parallélisme ne change pas le résultat.</li>
              <li>Le filtre par bandes est approximatif : son rappel baisse quand beaucoup de bits changent ; le multi-probe et la vérification temporelle compensent. La recherche exhaustive reste la référence d’exactitude.</li>
            </ul>
          </section>
        </>
      )}
    </>
  );
}

function ChartCard({ title, children }: { title: string; children: React.ReactElement }) {
  return (
    <section className="card p-5">
      <h3 className="mb-3 text-sm font-semibold text-white">{title}</h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    </section>
  );
}
