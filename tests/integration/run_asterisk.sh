#!/bin/sh
# Integrationstest gegen eine ECHTE lokale Asterisk-Instanz (Image aus docker-compose.yml), ohne Fritzbox und ohne Netz:
# Pod mit --network none (nur localhost). Darin:
#   - Asterisk mit der gerenderten Konfiguration (asterisk/render.py, Test-.env), fritz.conf zeigt auf die Gegenstelle
#   - Gegenstelle: zweite Asterisk-Instanz auf 127.0.0.1:5070 ("Telefone", tests/integration/callee)
#   - Bridge im Testprozess (lm-test-pytest) mit Fake-Gemini; Anrufe entstehen per AMI Originate ueber Local-Kanaele
# Es wird nie eine echte Nummer gewaehlt: alle Ziele enden in der Gegenstelle im Pod.
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
AST_IMAGE=${AST_IMAGE:-docker.io/andrius/asterisk:latest}
T=$(mktemp -d "${TMPDIR:-/tmp}/lm-itest.XXXXXX")
POD=lm-itest-$$
SECRET=integration-test-secret
cleanup() {
  if [ "${KEEP:-0}" != "1" ]; then
    podman pod rm -f "$POD" >/dev/null 2>&1 || true
    podman unshare rm -rf "$T" 2>/dev/null || rm -rf "$T"
  fi
}
trap cleanup EXIT
printf 'AMI_SECRET=%s\nWEB_PORT=18080\n' "$SECRET" > "$T/env"
mkdir -p "$T/conf" "$T/data"
podman run --rm --network none -v "$ROOT:/work:ro,z" -v "$T:/t:z" -e LM_ENV_FILE=/t/env -e LM_CONF_DIR=/t/conf -e LM_DATA_DIR=/t/data \
  lm-test python3 /work/asterisk/render.py
cp "$ROOT/tests/integration/fritz-test.conf" "$T/data/asterisk/fritz.conf"
cat "$ROOT/tests/integration/json-probe.conf" >> "$T/conf/extensions.conf"
podman pod create --name "$POD" --network none >/dev/null
podman run -d --pod "$POD" --name "$POD-ast" \
  -v "$T/conf/pjsip.conf:/etc/asterisk/pjsip.conf:ro,z" -v "$T/conf/extensions.conf:/etc/asterisk/extensions.conf:ro,z" \
  -v "$T/conf/modules.conf:/etc/asterisk/modules.conf:ro,z" -v "$T/conf/manager.conf:/etc/asterisk/manager.conf:ro,z" \
  -v "$T/conf/musiconhold.conf:/etc/asterisk/musiconhold.conf:ro,z" -v "$T/data/rec-in:/recordings:z" \
  -v "$T/data/sounds:/sounds:ro,z" -v "$T/data/asterisk:/etc/asterisk/dyn:ro,z" "$AST_IMAGE" >/dev/null
C="$ROOT/tests/integration/callee"
podman run -d --pod "$POD" --name "$POD-callee" \
  -v "$C/pjsip.conf:/etc/asterisk/pjsip.conf:ro,z" -v "$C/extensions.conf:/etc/asterisk/extensions.conf:ro,z" \
  -v "$C/modules.conf:/etc/asterisk/modules.conf:ro,z" -v "$C/manager.conf:/etc/asterisk/manager.conf:ro,z" "$AST_IMAGE" >/dev/null
rc=0
podman run --rm --pod "$POD" -v "$ROOT:/work:ro,z" -v "$T/data:/data:z" -w /work -e PYTHONDONTWRITEBYTECODE=1 \
  -e LM_TEST_DATA=/data -e AMI_SECRET="$SECRET" -e WEB_PORT=18080 -e LM_INTEG=1 \
  lm-test-pytest python -m pytest -p no:cacheprovider -q tests/integration "$@" || rc=$?
if [ "$rc" != "0" ]; then
  echo "--- Asterisk (letzte Zeilen) ---"
  podman logs "$POD-ast" 2>&1 | grep -v -E "^\s*$" | tail -60
fi
exit $rc
