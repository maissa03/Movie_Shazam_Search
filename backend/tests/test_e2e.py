"""Test de bout en bout : navigateur -> Next.js -> FastAPI -> moteur réel -> SQLite -> résultat affiché.

Prérequis : ``npm install`` et ``npm run build`` dans ``frontend/`` (le build fige l'URL de l'API
``http://127.0.0.1:8000``), Playwright (``pip install playwright``) et Microsoft Edge ou Chromium.
Le port 8000 doit être libre ; le frontend utilise 3000, ou le premier port libre suivant
(l'origine correspondante est alors autorisée par CORS).  Lancer : ``pytest -m e2e``.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from conftest import ROOT, needs_ffmpeg

pytestmark = [needs_ffmpeg, pytest.mark.e2e]
FRONT = ROOT / "frontend"
API = "http://127.0.0.1:8000"


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _wait(url: str, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=3).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError(url)


def _env(data: Path) -> dict:
    return {**os.environ, "MEDIA_ROOT": str(data / "media"), "TEMP_ROOT": str(data / "temp"),
            "DATABASE_URL": f"sqlite:///{(data / 'db.sqlite').as_posix()}", "NEXT_TELEMETRY_DISABLED": "1",
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


@pytest.fixture
def stack(tmp_path: Path):
    if not (FRONT / ".next" / "BUILD_ID").exists():
        pytest.skip("frontend non construit : cd frontend && npm install && npm run build")
    if not _port_free(8000):
        pytest.skip("port 8000 occupé : arrêtez l'application avant le test e2e")
    web_port = next((p for p in range(3000, 3100) if _port_free(p)), None)
    if web_port is None:
        pytest.skip("aucun port libre entre 3000 et 3099 pour le frontend")
    pytest.importorskip("playwright")
    web = f"http://127.0.0.1:{web_port}"
    data = tmp_path / "e2e"
    env = {**_env(data), "FRONTEND_ORIGINS": json.dumps([web, f"http://localhost:{web_port}"])}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    log = open(tmp_path / "backend.log", "w", encoding="utf-8")  # noqa: SIM115
    backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                "--port", "8000"], cwd=ROOT / "backend", env=env, stdout=log,
                               stderr=subprocess.STDOUT, creationflags=flags)
    front = subprocess.Popen(["node", str(FRONT / "node_modules" / "next" / "dist" / "bin" / "next"), "start",
                              "-H", "127.0.0.1", "-p", str(web_port)], cwd=FRONT, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait(f"{API}/api/health")
        _wait(web)
        yield web
    finally:
        front.terminate()
        # arrêt gracieux du backend (Ctrl+Break sous Windows, SIGINT ailleurs) : gestionnaire on_shutdown
        backend.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        try:
            backend.wait(timeout=60)
        except subprocess.TimeoutExpired:
            backend.kill()
        front.wait(timeout=30)
        log.close()


def test_browser_to_engine_and_back(stack, mini_corpus):
    web = stack
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    corpus, gt = mini_corpus
    indexed = [m for m in gt["movies"] if m["indexed"]]
    known = next(c for c in gt["clips"] if c["expected"] == "match" and c["transform"] == "clean")
    unknown = next(c for c in gt["clips"] if c["expected"] == "no_match")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except PlaywrightError:
            try:
                browser = p.chromium.launch(headless=True)
            except PlaywrightError:
                pytest.skip("ni Edge ni Chromium disponible pour Playwright")
        page = browser.new_page()
        # 1) import par l'interface (glisser-déposer = input file)
        page.goto(f"{web}/bibliotheque")
        page.set_input_files("input[type=file]", [str(corpus / m["file"]) for m in indexed])
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            movies = httpx.get(f"{API}/api/movies").json()
            if len(movies) == len(indexed) and all(m["status"] == "indexed" for m in movies):
                break
            time.sleep(0.5)
        else:
            pytest.fail(f"indexation non terminée : {movies}")
        page.reload()
        page.wait_for_selector("text=Indexé")
        assert page.locator("text=Indexé").count() >= len(indexed)

        # 2) reconnaissance d'un clip connu
        page.goto(f"{web}/reconnaissance")
        page.set_input_files("input[type=file]", str(corpus / known["file"]))
        page.get_by_role("button", name="Identifier le film").click()
        page.wait_for_selector("text=Film identifié", timeout=60_000)
        body = page.inner_text("main")
        source_file = next(m["file"] for m in gt["movies"] if m["key"] == known["source_key"])
        assert Path(source_file).stem.replace("_", " ") in body  # titre déduit du nom de fichier importé
        s = known["start_ms"] // 1000
        assert f"00:00:{s:02d}" in body  # début affiché au format HH:MM:SS

        # 3) clip inconnu : refus honnête
        page.goto(f"{web}/reconnaissance")
        page.set_input_files("input[type=file]", str(corpus / unknown["file"]))
        page.get_by_role("button", name="Identifier le film").click()
        page.wait_for_selector("text=Aucune correspondance suffisamment fiable", timeout=60_000)

        # 4) la page Traitements montre les jobs réels
        page.goto(f"{web}/traitements")
        page.wait_for_selector("text=Indexation")
        assert page.locator("text=Terminé").count() >= len(indexed)
        browser.close()


def test_backend_graceful_shutdown(tmp_path: Path):
    """Ctrl+Break / SIGINT -> gestionnaire d'arrêt : jobs, pool, threads et écrivain arrêtés proprement."""
    if not _port_free(8000):
        pytest.skip("port 8000 occupé")
    log_path = tmp_path / "backend.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with open(log_path, "w", encoding="utf-8") as log:
        backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                    "--port", "8000"], cwd=ROOT / "backend", env=_env(tmp_path / "data"),
                                   stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        try:
            _wait(f"{API}/api/health")
            pids = httpx.get(f"{API}/api/health").json()["concurrency"]["worker_pids"]
            assert len(pids) >= 1
        finally:
            backend.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            code = backend.wait(timeout=60)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    assert "arrêt terminé" in text, text[-2000:]
    assert code == 0 or os.name == "nt"  # Ctrl+Break peut donner un code non nul sous Windows
    import psutil
    time.sleep(1)
    assert not [p for p in pids if psutil.pid_exists(p)]  # aucun processus du pool orphelin
