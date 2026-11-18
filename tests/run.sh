#!/bin/sh
# Testmappe ausfuehren - alles in podman, ohne Netz fuer die Tests, ohne echte Zugangsdaten.
#   tests/run.sh                 Unit- und Szenariotests (Bridge mit Fake-Gemini, Fake-AudioSocket, AMI-Stub)
#   tests/run.sh --asterisk      zusaetzlich der Test gegen eine echte lokale Asterisk-Instanz (eigener Pod ohne Netz, keine Fritzbox)
#   tests/run.sh -- -k route     weitere Argumente gehen an pytest
# Images: lm-test (aus bridge/Dockerfile) und lm-test-pytest (lm-test + pytest, tests/Containerfile).
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
AST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --asterisk) AST=1; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done
if [ "${LM_SKIP_BUILD:-0}" != "1" ]; then
  podman build -q -t lm-test "$ROOT/bridge" >/dev/null
  podman build -q -t lm-test-pytest -f "$ROOT/tests/Containerfile" "$ROOT/tests" >/dev/null
fi
# Unit-/Szenariotests: --network none (kein Gemini, keine Fritzbox erreichbar)
podman run --rm --network none -v "$ROOT:/work:ro,z" -w /work -e PYTHONDONTWRITEBYTECODE=1 \
  lm-test-pytest python -m pytest -p no:cacheprovider -q tests --ignore=tests/integration "$@"
if [ "$AST" = "1" ]; then
  sh "$ROOT/tests/integration/run_asterisk.sh"
fi
