"""Benchmarks académiques reproductibles de MovieShazam (aucune valeur inventée).

Mesures :
  1. indexation du même corpus en séquentiel, 1, 2, 4 processus (speedup, efficacité,
     images/s, empreintes/s, RAM, cœurs occupés, empreintes identiques) ;
  2. (``--lowres``) même chose sur une version 480x270 du corpus : le décodage y est limité
     par le CPU et non par la bande passante mémoire ;
  3. (``--decode-experiment``) décodage FFmpeg brut 1/2/4 processus à 270p, 720p et 1080p :
     explique *pourquoi* le speedup dépend de la résolution sur cette machine ;
  4. recherche : index par bandes vs exhaustive sur les clips de vérité terrain (temps,
     exactitude), rappel du filtre par bandes vs distance, et passage à l'échelle avec des
     empreintes « distractrices » synthétiques (base jetable).

Les résultats sont écrits dans ``docs/results/`` (JSON + CSV + Markdown) et, sauf
``--no-app-db``, ajoutés à la table ``benchmarks`` de l'application (page Performances).

Usage :
    python scripts/run_benchmark.py                       # rapide (1 répétition)
    python scripts/run_benchmark.py --repeats 3 --lowres --decode-experiment
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from sqlalchemy import insert, select  # noqa: E402

from app.concurrency.thread_io import sha256_file  # noqa: E402
from app.core.settings import Settings, get_settings  # noqa: E402
from app.db import repo  # noqa: E402
from app.db.models import Fingerprint, FingerprintBand, Movie, Segment  # noqa: E402
from app.db.session import init_db, make_engine, make_session_factory  # noqa: E402
from app.db.writer import DbWriter  # noqa: E402
from app.services import benchmark as bench  # noqa: E402
from app.services import searching  # noqa: E402
from app.video.ffmpeg_io import find_binary, probe_video  # noqa: E402
from app.video.fingerprint import bands, to_blob  # noqa: E402
from app.video.matching import MatchParams  # noqa: E402


def corpus_items(corpus: Path, gt: dict) -> list[bench.CorpusItem]:
    items = []
    for m in [m for m in gt["movies"] if m["indexed"]]:
        p = corpus / m["file"]
        items.append(bench.CorpusItem(p, probe_video(p).duration_ms, m["title"], sha256_file(p)))
    return items


def transcode(items: list[bench.CorpusItem], out_dir: Path, scale: str) -> list[bench.CorpusItem]:
    out_dir.mkdir(parents=True, exist_ok=True)
    res = []
    for it in items:
        dst = out_dir / it.path.name
        if not dst.exists():
            subprocess.run([find_binary("ffmpeg"), "-y", "-loglevel", "error", "-i", str(it.path), "-vf",
                            f"scale={scale}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", "50",
                            str(dst)], check=True)
        res.append(bench.CorpusItem(dst, probe_video(dst).duration_ms, it.label + f" ({scale})", sha256_file(dst)))
    return res


def print_index_table(title: str, result: dict) -> None:
    print(f"\n=== {title} : {len(result['corpus'])} films, {result['corpus_duration_ms'] / 1000:.0f} s de vidéo, "
          f"{result['segments']} segments ===")
    print(f"{'mode':<14}{'temps (s)':>10}{'speedup':>9}{'effic.':>8}{'images/s':>10}{'empr./s':>9}"
          f"{'RAM pic Mo':>11}{'cœurs':>7}{'écriture ms':>12}  identiques  PID")
    for x in result["summary"]:
        print(f"{x['label']:<14}{x['elapsed_ms_median'] / 1000:>10.2f}{x['speedup']:>9.2f}{x['efficiency']:>8.2f}"
              f"{x['frames_per_s']:>10.0f}{x['fingerprints_per_s']:>9.1f}{x['peak_ram_mb']:>11.0f}"
              f"{x['busy_cores_avg']:>7.1f}{x['write_ms_median']:>12.0f}  {str(x['identical_to_sequential']):<10}  "
              f"{x['distinct_pids']}")


def decode_experiment(items: list[bench.CorpusItem], work: Path) -> list[dict]:
    """Décodage FFmpeg brut : N copies simultanées du même travail, débit relatif."""
    src = items[0].path
    variants = {"480x270": transcode([items[0]], work / "270p", "480:270")[0].path, "1280x720": src,
                "1920x1080": transcode([items[0]], work / "1080p", "1920:1080")[0].path}
    out = []
    for label, path in variants.items():
        cmd = [find_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-threads", "1", "-i", str(path),
               "-f", "null", "-"]
        times = {}
        for n in (1, 2, 4):
            t0 = time.perf_counter()
            procs = [subprocess.Popen(cmd) for _ in range(n)]
            for p in procs:
                p.wait()
            times[n] = time.perf_counter() - t0
        for n, t in times.items():
            out.append({"resolution": label, "processes": n, "wall_s": round(t, 2),
                        "throughput_vs_1": round(n * times[1] / t, 2)})
        print(f"  décodage brut {label:>10} : " + ", ".join(
            f"{n} proc -> x{n * times[1] / t:.2f}" for n, t in times.items()))
    return out


def search_benchmark(corpus: Path, gt: dict, st: Settings, sf, keys: dict) -> dict:  # noqa: ANN001
    params = MatchParams()
    clips = gt["clips"]
    rows = []
    fps = {c["id"]: searching.fingerprint_clip(corpus / c["file"], st) for c in clips}
    for method in ("bands", "exhaustive"):
        times, correct = [], 0
        for c in clips:
            t0 = time.perf_counter()
            d, stats, _ = searching.search_clip(sf, fps[c["id"]], st, params, method)
            times.append((time.perf_counter() - t0) * 1000)
            ok = d.status == c["expected"] and (c["expected"] != "match" or (
                keys[d.best.movie_id] == c["source_key"] and abs(d.best.start_ms - c["start_ms"]) <= 1000))
            correct += ok
        rows.append({"method": method, "clips": len(clips), "correct": correct,
                     "search_ms_mean": round(statistics.fmean(times), 2),
                     "search_ms_median": round(statistics.median(times), 2)})
    return {"per_method": rows, "fingerprints": fps}


def scaling_benchmark(eval_db: Path, corpus: Path, gt: dict, st: Settings, keys: dict, fps: dict,
                      sizes: list[int], work: Path) -> list[dict]:
    """Ajoute des empreintes distractrices aléatoires dans une COPIE de l'index et compare."""
    out = []
    params = MatchParams()
    clips = [c for c in gt["clips"] if c["split"] == "test"]
    for size in sizes:
        db_copy = work / f"scaling_{size}.db"
        shutil.copyfile(eval_db, db_copy)
        engine = make_engine(db_copy)
        sf = make_session_factory(engine)
        if size:
            rng = random.Random(size)
            with sf() as s, s.begin():
                m = repo.create_movie(s, title="distracteurs", year=None, original_filename="-",
                                      stored_name=f"distract_{size}", file_size=0, file_mtime_ns=0,
                                      content_hash=f"distract{size}", duration_ms=size * 500, width=0, height=0,
                                      codec="-", fps=0, format_name="-", status="indexed",
                                      index_version=st.index_version)
                seg = Segment(movie_id=m.id, index_version=st.index_version, segment_index=0, start_ms=0,
                              end_ms=size * 500, status="done", fingerprints_count=size)
                s.add(seg)
                s.flush()
                for start in range(0, size, 20_000):
                    chunk = range(start, min(size, start + 20_000))
                    hashes = [rng.getrandbits(64) for _ in chunk]
                    s.execute(insert(Fingerprint), [
                        {"movie_id": m.id, "segment_id": seg.id, "timestamp_ms": i * 500, "hash_blob": to_blob(h),
                         "informativeness": 50.0, "algorithm_version": st.index_version}
                        for i, h in zip(chunk, hashes)])
                    ids = {ts: fid for ts, fid in s.execute(
                        select(Fingerprint.timestamp_ms, Fingerprint.id).where(
                            Fingerprint.movie_id == m.id, Fingerprint.timestamp_ms >= start * 500,
                            Fingerprint.timestamp_ms < (start + 20_000) * 500))}
                    s.execute(insert(FingerprintBand), [
                        {"band_index": b, "band_value": v, "fingerprint_id": ids[i * 500]}
                        for i, h in zip(chunk, hashes) for b, v in enumerate(bands(h))])
        with sf() as s:
            total = s.query(Fingerprint).join(Movie).filter(Movie.status == "indexed").count()
        res = {"distractors": size, "index_fingerprints": total}
        for method in ("bands", "exhaustive"):
            times, correct = [], 0
            for c in clips:
                t0 = time.perf_counter()
                d, _, _ = searching.search_clip(sf, fps[c["id"]], st, params, method)
                times.append((time.perf_counter() - t0) * 1000)
                correct += d.status == c["expected"] and (c["expected"] != "match" or keys.get(d.best.movie_id)
                                                          == c["source_key"])
            res[f"{method}_ms_median"] = round(statistics.median(times), 1)
            res[f"{method}_correct"] = f"{correct}/{len(clips)}"
        out.append(res)
        print(f"  index {total:>8} empreintes : bandes {res['bands_ms_median']:>8.1f} ms ({res['bands_correct']})"
              f" | exhaustive {res['exhaustive_ms_median']:>8.1f} ms ({res['exhaustive_correct']})")
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(str(db_copy) + suffix).unlink(missing_ok=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=ROOT / "data" / "demo")
    ap.add_argument("--modes", type=int, nargs="+", default=bench.default_modes())
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--lowres", action="store_true", help="aussi sur une version 480x270 du corpus")
    ap.add_argument("--decode-experiment", action="store_true")
    ap.add_argument("--scaling", type=int, nargs="*", default=[0, 50_000, 200_000],
                    help="tailles d'empreintes distractrices (vide pour ignorer)")
    ap.add_argument("--no-app-db", action="store_true", help="ne pas enregistrer dans la base de l'application")
    ap.add_argument("--skip-index", action="store_true", help="ne mesurer que la recherche")
    args = ap.parse_args()

    st = get_settings()
    st.ensure_dirs()
    gt = json.loads((args.corpus / "ground_truth.json").read_text(encoding="utf-8"))
    work = ROOT / "data" / "bench"
    work.mkdir(parents=True, exist_ok=True)
    machine = bench.machine_info(st)
    print("Machine :", json.dumps({k: v for k, v in machine.items() if k != "engine"}, ensure_ascii=False))
    report: dict = {"date": datetime.now().isoformat(timespec="seconds"), "machine": machine,
                    "modes": args.modes, "repeats": args.repeats}
    run_ids = []

    items = corpus_items(args.corpus, gt)
    if not args.skip_index:
        res = bench.run_index_benchmark(items, st, args.modes, args.repeats, scratch_dir=work)
        print_index_table("Indexation, corpus de démonstration (1280x720)", res)
        report["index_720p"] = res
        run_ids.append((uuid.uuid4().hex, bench.dataset_id(items, st), res))

    if args.lowres and not args.skip_index:
        low = transcode(items, work / "lowres", "480:270")
        res_low = bench.run_index_benchmark(low, st, args.modes, args.repeats, scratch_dir=work)
        print_index_table("Indexation, même corpus en 480x270", res_low)
        report["index_270p"] = res_low
        run_ids.append((uuid.uuid4().hex, bench.dataset_id(low, st), res_low))

    if args.decode_experiment:
        print("\nExpérience : décodage FFmpeg brut, N copies simultanées")
        report["decode_experiment"] = decode_experiment(items, work)

    print("\nRecherche : index par bandes vs recherche exhaustive")
    from evaluate import build_index

    eval_dir = ROOT / "data" / "eval" / args.corpus.name
    est, esf, ewriter, keys = build_index(args.corpus, gt, eval_dir, 2)
    try:
        sb = search_benchmark(args.corpus, gt, est, esf, keys)
        for r in sb["per_method"]:
            print(f"  {r['method']:<11}: {r['correct']}/{r['clips']} corrects, médiane {r['search_ms_median']} ms "
                  f"(empreintes du clip comprises : non ; candidats + alignement uniquement)")
        report["search"] = sb["per_method"]
        recall = bench.run_band_recall_benchmark(esf, est, MatchParams())
        report["band_recall"] = recall
        print("  rappel du filtre par bandes (sans / avec multi-probe) :")
        for d in sorted({x["distance"] for x in recall["curve"]}):
            r0 = next(x for x in recall["curve"] if x["distance"] == d and x["probe_radius"] == 0)
            r1 = next(x for x in recall["curve"] if x["distance"] == d and x["probe_radius"] == 1)
            print(f"    d={d:>2} : {r0['recall']:.2f} / {r1['recall']:.2f}")
        if args.scaling:
            print("  passage à l'échelle (empreintes distractrices aléatoires, base jetable) :")
            report["search_scaling"] = scaling_benchmark(est.database_path, args.corpus, gt, est, keys,
                                                         sb["fingerprints"], args.scaling, work)
    finally:
        ewriter.close()

    out = ROOT / "docs" / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out / f"benchmark_{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "benchmark_latest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(out / f"benchmark_{stamp}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["corpus", "mode", "process_count", "elapsed_ms_median", "speedup", "efficiency", "frames_per_s",
                    "fingerprints_per_s", "peak_ram_mb", "busy_cores_avg", "write_ms_median",
                    "identical_to_sequential"])
        for key in ("index_720p", "index_270p"):
            for x in report.get(key, {}).get("summary", []):
                w.writerow([key, x["mode"], x["process_count"], x["elapsed_ms_median"], x["speedup"], x["efficiency"],
                            x["frames_per_s"], x["fingerprints_per_s"], x["peak_ram_mb"], x["busy_cores_avg"],
                            x["write_ms_median"], x["identical_to_sequential"]])
    print(f"\nRésultats : {out / f'benchmark_{stamp}.json'}")

    if not args.no_app_db and run_ids:
        engine = make_engine(st.database_path)
        init_db(engine)
        writer = DbWriter(make_session_factory(engine))
        writer.start()
        try:
            for i, (rid, ds, r) in enumerate(run_ids):
                rows = bench.summarize_for_db(rid, ds, r, machine)
                if i == 0 and "band_recall" in report:
                    rows.append({"run_id": rid, "kind": "search", "mode": "bands-recall", "process_count": 1,
                                 "dataset_id": ds, "elapsed_ms": report["band_recall"].get(
                                     "exhaustive_ms_same_queries", 0) or 0,
                                 "metrics_json": json.dumps({**report["band_recall"],
                                                             "search": report.get("search"),
                                                             "scaling": report.get("search_scaling")})})
                writer.run_sync(lambda s, rows=rows: repo.add_benchmark_rows(s, rows))
            print(f"Enregistré dans la base de l'application ({st.database_path.name}) : page « Performances ».")
        finally:
            writer.close()
            engine.dispose()


if __name__ == "__main__":
    main()
