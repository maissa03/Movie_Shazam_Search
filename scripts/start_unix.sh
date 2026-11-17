#!/usr/bin/env bash
# Démarre MovieShazam sur CE PC (Linux / macOS) : backend FastAPI 127.0.0.1:8000 + frontend Next.js 127.0.0.1:3000.
# Première exécution : crée .venv, installe les dépendances exactes et construit le frontend.
# Un seul processus Uvicorn ; aucune écoute réseau hors 127.0.0.1 ; aucune vidéo supprimée.
# Usage : bash scripts/start_unix.sh [--dev] [--setup-only]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV=0; SETUP_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    --setup-only) SETUP_ONLY=1 ;;
    *) echo "option inconnue : $arg"; exit 2 ;;
  esac
done

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
show() { printf '    $ %s\n' "$*"; }
fail() { printf '\n\033[31mERREUR : %s\033[0m\n' "$1"; exit 1; }
run() { show "$*"; "$@" || fail "commande échouée : $*"; }

step "Vérification des outils"
PY=""
for c in python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] in ((3,11),(3,12)) else 1)'; then PY="$c"; break; fi
done
[ -x "$ROOT/.venv/bin/python" ] || [ -n "$PY" ] || fail "Python 3.11 ou 3.12 introuvable (ex. sudo apt install python3.12 python3.12-venv)"
command -v node >/dev/null || fail "Node.js introuvable (https://nodejs.org ou gestionnaire de paquets)"
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || fail "FFmpeg introuvable (sudo apt install ffmpeg / brew install ffmpeg)"
echo "    Node.js $(node --version) · $(ffmpeg -version | head -n1)"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  step "Création de l'environnement virtuel Python (.venv)"
  run "$PY" -m venv "$ROOT/.venv"
  run "$ROOT/.venv/bin/python" -m pip install --upgrade pip
  run "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
fi
[ -f "$ROOT/backend/.env" ] || { step "Création de backend/.env"; cp "$ROOT/backend/.env.example" "$ROOT/backend/.env"; }
mkdir -p "$ROOT/data/media" "$ROOT/data/temp"

export NEXT_TELEMETRY_DISABLED=1
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  step "Installation des dépendances du frontend"
  (cd "$ROOT/frontend" && run npm ci --no-audit --no-fund)
fi
if [ "$DEV" = 0 ] && [ ! -f "$ROOT/frontend/.next/BUILD_ID" ]; then
  step "Construction du frontend (production)"
  (cd "$ROOT/frontend" && run npm run build)
fi
[ "$SETUP_ONLY" = 1 ] && { step "Installation terminée."; exit 0; }

step "Démarrage du backend FastAPI (127.0.0.1:8000)"
show "cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
(cd "$ROOT/backend" && exec "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
BACK_PID=$!
cleanup() {
  echo; echo "Arrêt de MovieShazam..."
  kill -INT "$BACK_PID" 2>/dev/null || true   # SIGINT : arrêt propre (jobs, pool, écritures SQLite)
  wait "$BACK_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 90); do
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
  kill -0 "$BACK_PID" 2>/dev/null || fail "le backend s'est arrêté (voir les messages ci-dessus)"
  sleep 1
done
echo "    Backend prêt : http://127.0.0.1:8000/docs"

step "Démarrage du frontend Next.js (127.0.0.1:3000) — Ctrl+C pour tout arrêter"
cd "$ROOT/frontend"
if [ "$DEV" = 1 ]; then show "npm run dev"; npm run dev; else show "npm run start"; npm run start; fi
