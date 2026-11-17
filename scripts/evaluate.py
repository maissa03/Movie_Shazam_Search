"""Évaluation de la reconnaissance sur un corpus avec vérité terrain (+ calibration des seuils).

Protocole :
  1. ``--split validation --calibrate`` : mesure les scores des vraies correspondances et des
     candidats incorrects **sur le split validation uniquement**, et en déduit les seuils ;
  2. les seuils sont recopiés dans ``MatchParams`` (code) puis **figés** ;
  3. ``--split test`` : évaluation finale, jamais utilisée pour régler quoi que ce soit.

Usage :
    python scripts/evaluate.py --split validation --calibrate
    python scripts/evaluate.py --split test --method both
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.concurrency.process_pool import LocalProcessPool  # noqa: E402
from app.core.settings import Settings  # noqa: E402
from app.db import repo  # noqa: E402
from app.db.session import init_db, make_engine, make_session_factory  # noqa: E402
from app.db.writer import DbWriter  # noqa: E402
from app.services import indexing, library, searching  # noqa: E402
from app.video.matching import MatchParams  # noqa: E402


def build_index(corpus: Path, gt: dict, workdir: Path, workers: int) -> tuple[Settings, object, DbWriter, dict]:
    st = Settings(_env_file=None, database_url=f"sqlite:///{(workdir / 'eval.db').as_posix()}",
                  media_root=workdir / "media", temp_root=workdir / "temp")
    st.ensure_dirs()
    engine = make_engine(st.database_path)
    init_db(engine)
    sf = make_session_factory(engine)
    writer = DbWriter(sf)
    writer.start()
    keys: dict[int, str] = {}
    pool = None
    for m in [m for m in gt["movies"] if m["indexed"]]:
        staged = library.stage_local_file(corpus / m["file"], st)
        movie, created = writer.run_sync(lambda s, stg=staged, mm=m: library.register_staged(s, stg, st, mm["title"],
                                                                                           mm["year"]))
        if not created:
            staged.temp_path.unlink(missing_ok=True)
        keys[movie.id] = m["key"]
        if movie.status != "indexed" or movie.index_version != st.index_version:
            if pool is None:
                pool = LocalProcessPool(workers)
                pool.start()
            rep = indexing.index_movie_sync(movie.id, library.movie_path(movie, st), movie.duration_ms, st, writer,
                                            pool)
            print(f"  indexé {m['key']} ({m['title']}) : {rep.segments_computed} segments, {rep.elapsed_s:.1f} s")
    if pool:
        pool.shutdown()
    return st, sf, writer, keys


def evaluate(corpus: Path, gt: dict, st: Settings, sf, keys: dict, split: str, method: str,  # noqa: ANN001
             params: MatchParams) -> dict:
    rows = []
    for clip in [c for c in gt["clips"] if split == "all" or c["split"] == split]:
        t0 = time.perf_counter()
        fp = searching.fingerprint_clip(corpus / clip["file"], st)
        decision, stats, _ = searching.search_clip(sf, fp, st, params, method)
        elapsed = (time.perf_counter() - t0) * 1000
        cands = [{"key": keys.get(h.movie_id, "?"), "start_ms": h.start_ms, "score": h.score,
                  "coverage": h.coverage, "span": h.span_ratio, "mean_d": h.mean_distance}
                 for h in decision.candidates]
        best = decision.best
        err = None
        ok = decision.status == clip["expected"]
        if ok and clip["expected"] == "match":
            err = best.start_ms - clip["start_ms"]
            ok = keys[best.movie_id] == clip["source_key"] and abs(err) <= gt["tolerance_ms"]
        rows.append({"id": clip["id"], "split": clip["split"], "transform": clip["transform"], "note": clip["note"],
                     "expected": clip["expected"], "got": decision.status, "correct": ok, "error_ms": err,
                     "truth_key": clip["source_key"], "truth_start_ms": clip["start_ms"],
                     "best_key": keys.get(best.movie_id) if best else None, "candidates": cands,
                     "reason": decision.reason, "candidates_found": stats.candidates,
                     "candidate_ms": round(stats.elapsed_ms, 1), "total_ms": round(elapsed, 1)})
    n_ok = sum(r["correct"] for r in rows)
    errs = [abs(r["error_ms"]) for r in rows if r["error_ms"] is not None]
    return {"split": split, "method": method, "clips": len(rows), "correct": n_ok,
            "accuracy": round(n_ok / max(len(rows), 1), 4),
            "timestamp_error_ms": {"mean": round(statistics.fmean(errs), 1) if errs else None,
                                   "max": max(errs) if errs else None},
            "mean_search_ms": round(statistics.fmean(r["total_ms"] for r in rows), 1), "rows": rows,
            "params": params.to_dict()}


def calibrate(report: dict) -> dict:
    """Seuils déduits des distributions de scores du split validation."""
    pos, neg, ratios = [], [], []
    for r in report["rows"]:
        c = r["candidates"]
        if r["expected"] == "match":
            true = [x for x in c if x["key"] == r["truth_key"] and abs(x["start_ms"] - r["truth_start_ms"]) <= 1000]
            false = [x for x in c if x not in true]
            if true:
                pos.append(true[0]["score"])
                if false:
                    ratios.append(false[0]["score"] / true[0]["score"])
            neg += [x["score"] for x in false]
        elif r["expected"] == "no_match":
            neg += [x["score"] for x in c]
    lo, hi = max(neg or [0.0]), min(pos or [1.0])
    accept = round(((lo + hi) / 2) * 20) / 20  # milieu de l'écart, arrondi à 0,05
    worst_ratio = max(ratios or [0.0])
    return {"positive_scores": sorted(pos), "negative_scores": sorted(neg, reverse=True)[:10],
            "max_negative": lo, "min_positive": hi, "gap": round(hi - lo, 4),
            "suggested_accept_score": accept, "max_wrong_over_true_ratio": round(worst_ratio, 4),
            "suggested_ambiguity_ratio": 0.85 if worst_ratio < 0.8 else round(min(0.95, worst_ratio + 0.05), 2),
            "rule": "accept_score = milieu entre le pire négatif et le pire positif (arrondi 0,05) ; "
                    "ratio d'ambiguïté > plus grand rapport (candidat faux / vrai) observé"}


def to_markdown(rep: dict) -> str:
    lines = [f"### Split `{rep['split']}` — méthode `{rep['method']}` : {rep['correct']}/{rep['clips']} corrects "
             f"(erreur d'horodatage moyenne {rep['timestamp_error_ms']['mean']} ms, max "
             f"{rep['timestamp_error_ms']['max']} ms ; recherche moyenne {rep['mean_search_ms']} ms)", "",
             "| Clip | Transformation | Attendu | Obtenu | Erreur (ms) | Score meilleur | 2e candidat | Remarque |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        c = r["candidates"]
        best = f"{c[0]['key']}@{c[0]['start_ms'] / 1000:.1f}s {c[0]['score']:.3f}" if c else "—"
        second = f"{c[1]['key']}@{c[1]['start_ms'] / 1000:.1f}s {c[1]['score']:.3f}" if len(c) > 1 else "—"
        mark = "✅" if r["correct"] else "❌"
        lines.append(f"| {r['id']} | {r['transform']} | {r['expected']} | {mark} {r['got']} | "
                     f"{r['error_ms'] if r['error_ms'] is not None else '—'} | {best} | {second} | {r['note']} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=ROOT / "data" / "demo")
    ap.add_argument("--split", choices=["validation", "test", "all"], default="test")
    ap.add_argument("--method", choices=["bands", "exhaustive", "both"], default="bands")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "results")
    args = ap.parse_args()
    gt = json.loads((args.corpus / "ground_truth.json").read_text(encoding="utf-8"))
    workdir = ROOT / "data" / "eval" / args.corpus.name
    print(f"Index d'évaluation : {workdir}")
    st, sf, writer, keys = build_index(args.corpus, gt, workdir, args.workers)
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        methods = ["bands", "exhaustive"] if args.method == "both" else [args.method]
        md = []
        for method in methods:
            params = MatchParams()
            if args.calibrate:
                params = MatchParams(accept_score=0.0, min_coverage=0.0, min_span_ratio=0.0, min_matched_frames=0)
            rep = evaluate(args.corpus, gt, st, sf, keys, args.split, method, params)
            (args.out / f"eval_{args.split}_{method}.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                                                                      encoding="utf-8")
            md.append(to_markdown(rep))
            print(f"[{method}] {args.split}: {rep['correct']}/{rep['clips']} corrects, erreur moyenne "
                  f"{rep['timestamp_error_ms']['mean']} ms, recherche moyenne {rep['mean_search_ms']} ms")
            if args.calibrate:
                print("   (mode calibration : seuils de décision désactivés, seuls les scores comptent)")
            for r in rep["rows"]:
                if not r["correct"] and not args.calibrate:
                    print(f"   ÉCHEC {r['id']}: attendu {r['expected']}, obtenu {r['got']} — {r['reason']}")
            if args.calibrate:
                cal = calibrate(rep)
                (args.out / "calibration.json").write_text(json.dumps(cal, indent=2), encoding="utf-8")
                print("Calibration (validation) :", json.dumps({k: v for k, v in cal.items()
                                                                if not k.endswith("scores")}, indent=2))
        (args.out / f"eval_{args.split}.md").write_text("\n\n".join(md) + "\n", encoding="utf-8")
        with sf() as s:
            n = len(repo.list_movies(s, status="indexed"))
        print(f"Rapports écrits dans {args.out} ({n} films indexés)")
    finally:
        writer.close()


if __name__ == "__main__":
    main()
