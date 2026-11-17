#!/usr/bin/env bash
# Contrôle qualité : ruff + pytest + tsc.  --all inclut les tests longs et le test de bout en bout.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"
MARK="not slow and not e2e"; [ "${1:-}" = "--all" ] && MARK="slow or not slow"
status=0
(cd "$ROOT/backend" && "$PY" -m ruff check app tests ../scripts) || status=1
(cd "$ROOT/backend" && "$PY" -m pytest -q -m "$MARK") || status=1
(cd "$ROOT/frontend" && npx tsc --noEmit) || status=1
[ $status = 0 ] && echo "Contrôle qualité : tout est vert." || echo "Des vérifications ont échoué."
exit $status
