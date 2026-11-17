"""Tests d'intégration : FastAPI réel + moteur réel + SQLite + vraies vidéos générées."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import make_settings, needs_ffmpeg
from fastapi.testclient import TestClient

from app.main import create_app

pytestmark = needs_ffmpeg
TOLERANCE_MS = 1000  # ±1 s sur le cas simple à 2 images/s (en pratique < 100 ms, voir docs)


def wait_job(client: TestClient, job_id: str, timeout: float = 180) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.2)  # polling HTTP, comme le frontend
    raise TimeoutError(job_id)


def wait_search(client: TestClient, search_id: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = client.get(f"/api/search/{search_id}").json()
        if s["status"] not in ("queued", "running"):
            return s
        time.sleep(0.2)
    raise TimeoutError(search_id)


def upload(client: TestClient, path: Path, **data) -> dict:
    with open(path, "rb") as f:
        r = client.post("/api/movies", files={"file": (path.name, f, "video/mp4")}, data=data)
    assert r.status_code in (200, 202), r.text
    return r.json()


def search(client: TestClient, path: Path, method: str = "bands") -> dict:
    with open(path, "rb") as f:
        r = client.post("/api/search", files={"file": (path.name, f, "video/mp4")}, data={"method": method})
    assert r.status_code == 202, r.text
    return wait_search(client, r.json()["search_id"])


@pytest.fixture
def app_settings(tmp_path: Path):
    return make_settings(tmp_path / "app", process_workers=2)


def test_index_and_recognize_real_videos(mini_corpus, app_settings):
    corpus, gt = mini_corpus
    with TestClient(create_app(app_settings)) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        ids = {}
        for m in [m for m in gt["movies"] if m["indexed"]]:
            out = upload(client, corpus / m["file"], title=m["title"])
            job = wait_job(client, out["job"]["id"])
            assert job["state"] == "completed", job["error"]
            assert job["completed_units"] == job["total_units"] > 0
            assert len(job["worker_pids"]) == 2  # deux processus locaux distincts ont contribué
            ids[out["movie"]["id"]] = m["key"]
        for clip in gt["clips"]:
            res = search(client, corpus / clip["file"])
            assert res["status"] == clip["expected"], (clip["id"], res["result"].get("reason"))
            if clip["expected"] == "match":
                assert ids[res["best_movie_id"]] == clip["source_key"]
                assert abs(res["start_ms"] - clip["start_ms"]) <= TOLERANCE_MS
                assert 0 < res["score"] <= 1
        stats = client.get("/api/stats").json()
        assert stats["movies_indexed"] == 2 and stats["fingerprints_total"] > 0


def test_repeat_import_and_reindex_do_not_recompute(mini_corpus, app_settings):
    corpus, gt = mini_corpus
    path = corpus / gt["movies"][0]["file"]
    with TestClient(create_app(app_settings)) as client:
        first = upload(client, path)
        wait_job(client, first["job"]["id"])
        again = upload(client, path)
        assert again["created"] is False and again["job"] is None
        assert again["movie"]["id"] == first["movie"]["id"]
        r = client.post(f"/api/movies/{first['movie']['id']}/reindex")
        assert r.status_code == 200 and r.json()["job"] is None
        assert len(client.get("/api/movies").json()) == 1
        forced = client.post(f"/api/movies/{first['movie']['id']}/reindex?force=true")
        assert forced.status_code == 202
        job = wait_job(client, forced.json()["job"]["id"])
        assert job["state"] == "completed" and job["details"]["segments_reused"] == 0


def test_invalid_files_are_rejected(app_settings, tmp_path):
    fake = tmp_path / "pas_une_video.mp4"
    fake.write_bytes(b"ceci n'est pas une video" * 100)
    with TestClient(create_app(app_settings)) as client:
        with open(fake, "rb") as f:
            r = client.post("/api/movies", files={"file": (fake.name, f, "video/mp4")})
        assert r.status_code == 422 and "refusée" in r.json()["detail"]
        with open(fake, "rb") as f:
            r = client.post("/api/movies", files={"file": ("virus.exe", f, "application/octet-stream")})
        assert r.status_code == 422
        assert client.get("/api/movies").json() == []
        assert not list(app_settings.temp_dir.glob("*.part"))  # temporaires nettoyés


def test_ffmpeg_failure_marks_failed_and_api_stays_responsive(mini_corpus, app_settings, tmp_path):
    corpus, gt = mini_corpus
    data = (corpus / gt["movies"][0]["file"]).read_bytes()
    broken = tmp_path / "tronque.mp4"
    broken.write_bytes(data[: int(len(data) * 0.35)])
    with TestClient(create_app(app_settings)) as client:
        out = upload(client, broken)
        t0 = time.perf_counter()
        assert client.get("/api/health").status_code == 200  # l'API répond pendant le traitement
        assert time.perf_counter() - t0 < 2.0
        job = wait_job(client, out["job"]["id"])
        assert job["state"] == "failed" and "segment" in job["error"]
        movie = client.get(f"/api/movies/{out['movie']['id']}").json()
        assert movie["status"] == "failed" and movie["error"]
        res = search(client, corpus / gt["clips"][0]["file"])
        assert res["status"] == "no_match"  # un index en échec n'est jamais utilisé


def test_restart_keeps_movies_and_fingerprints(mini_corpus, app_settings):
    corpus, gt = mini_corpus
    movie = gt["movies"][0]
    clip = next(c for c in gt["clips"] if c["source_key"] == movie["key"] and c["expected"] == "match")
    with TestClient(create_app(app_settings)) as client:
        job_id = upload(client, corpus / movie["file"])["job"]["id"]
        wait_job(client, job_id)
    with TestClient(create_app(app_settings)) as client:  # « redémarrage » : nouvelle application
        movies = client.get("/api/movies").json()
        assert len(movies) == 1 and movies[0]["status"] == "indexed"
        # le job n'est plus en mémoire : lu en base, il doit être dans son état FINAL
        # (garantit qu'un instantané plus ancien n'a pas écrasé l'état terminal)
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["state"] == "completed" and job["completed_units"] == job["total_units"] > 0
        res = search(client, corpus / clip["file"])
        assert res["status"] == "match" and abs(res["start_ms"] - clip["start_ms"]) <= TOLERANCE_MS


def test_delete_is_consistent_and_media_is_sandboxed(mini_corpus, app_settings):
    corpus, gt = mini_corpus
    with TestClient(create_app(app_settings)) as client:
        out = upload(client, corpus / gt["movies"][0]["file"])
        wait_job(client, out["job"]["id"])
        mid = out["movie"]["id"]
        assert client.get(f"/api/media/{mid}/thumbnail").status_code == 200
        assert client.get(f"/api/media/{mid}/video", headers={"Range": "bytes=0-9"}).status_code == 206
        stored = list(app_settings.movies_dir.iterdir())
        assert len(stored) == 1
        r = client.delete(f"/api/movies/{mid}")
        assert r.status_code == 200 and r.json()["deleted"]
        assert not list(app_settings.movies_dir.iterdir())
        assert client.get(f"/api/movies/{mid}").status_code == 404
        assert client.get("/api/media/..%2F..%2Fdb.sqlite/thumbnail").status_code in (404, 422)
        assert client.get("/api/stats").json()["fingerprints_total"] == 0


def test_cancel_then_resume_indexing(demo_corpus, tmp_path):
    corpus, gt = demo_corpus
    st = make_settings(tmp_path / "cancel", process_workers=1, max_inflight_segments_per_worker=1)
    with TestClient(create_app(st)) as client:
        out = upload(client, corpus / gt["movies"][0]["file"])
        job_id = out["job"]["id"]
        deadline = time.monotonic() + 60
        while client.get(f"/api/jobs/{job_id}").json()["completed_units"] < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        client.post(f"/api/jobs/{job_id}/cancel")
        job = wait_job(client, job_id)
        assert job["state"] == "cancelled"
        done = job["completed_units"]
        assert 1 <= done < job["total_units"]  # annulation coopérative entre segments
        movie = client.get(f"/api/movies/{out['movie']['id']}").json()
        assert movie["status"] == "cancelled"
        r = client.post(f"/api/movies/{out['movie']['id']}/reindex")
        job2 = wait_job(client, r.json()["job"]["id"])
        assert job2["state"] == "completed"
        assert job2["details"]["segments_reused"] == done  # reprise : rien n'est recalculé deux fois


def test_oversized_uploads_are_refused_with_413(app_settings, tmp_path):
    """Limites de taille (Starlette ``RequestBodyLimitMiddleware``) : 413 sans rien écrire sur disque."""
    st = make_settings(tmp_path / "small", max_clip_upload_mb=1)
    big = tmp_path / "gros_clip.mp4"
    big.write_bytes(b"\0" * (4 * 1024**2))  # 4 Mo > 1 Mo + marge multipart
    with TestClient(create_app(st)) as client:
        with open(big, "rb") as f:
            r = client.post("/api/search", files={"file": (big.name, f, "video/mp4")})
        # refus par le middleware AVANT lecture du corps (et non par la copie dans le handler)
        assert r.status_code == 413 and "Content Too Large" in r.text
        # limites propres à chaque route : les 4 Mo passent la limite des films (8 Go)
        # et atteignent la validation (ce ne sont pas de vraies vidéos -> 422)
        with open(big, "rb") as f:
            r = client.post("/api/movies", files={"file": (big.name, f, "video/mp4")})
        assert r.status_code == 422
        # limite globale (10 Mo) sur les routes JSON ordinaires
        r = client.patch("/api/movies/1", content=b"{" + b" " * (11 * 1024**2) + b"}",
                         headers={"Content-Type": "application/json"})
        assert r.status_code == 413
        assert client.get("/api/health").status_code == 200  # l'API reste disponible
        assert not list(st.temp_dir.glob("*.part"))  # aucun fichier temporaire laissé


def test_job_state_is_consistent_under_concurrent_polling(demo_corpus, tmp_path):
    """Les threads des requêtes lisent l'état des jobs pendant que les threads de traitement le
    modifient : chaque lecture doit être cohérente (verrou du JobManager)."""
    import threading

    corpus, gt = demo_corpus
    st = make_settings(tmp_path / "poll", process_workers=2)
    with TestClient(create_app(st)) as client:
        job_id = upload(client, corpus / gt["movies"][0]["file"])["job"]["id"]
        stop = threading.Event()
        errors: list[str] = []

        def poller() -> None:
            last = -1
            while not stop.is_set():
                for url in (f"/api/jobs/{job_id}", "/api/jobs", "/api/stats"):
                    r = client.get(url)
                    if r.status_code != 200:
                        errors.append(f"{url} -> {r.status_code}")
                job = client.get(f"/api/jobs/{job_id}").json()
                done, total = job["completed_units"], job["total_units"]
                if done < last or (total and done > total):
                    errors.append(f"progression incohérente : {last} -> {done}/{total}")
                last = done

        threads = [threading.Thread(target=poller) for _ in range(4)]
        for t in threads:
            t.start()
        try:
            final = wait_job(client, job_id)
        finally:
            stop.set()
            for t in threads:
                t.join()
        assert not errors, errors[:5]
        assert final["state"] == "completed" and final["completed_units"] == final["total_units"]
        assert len(final["worker_pids"]) == 2
