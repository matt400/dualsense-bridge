#!/bin/sh
# Testy na Linuksie (zaślepki Windows i urządzeń evdev): ./tests/run_tests.sh   (z katalogu głównego repo)
# Wymagania: python3 >= 3.11, pip install evdev pyflakes; UI: pip install playwright (Chromium); GUI: python3-tk + xvfb-run
set -u
cd "$(dirname "$0")/.."
T=tests; RX=receiver; TX=sender
export BTLOG="$(mktemp)" PATH="$PWD/$T/fakebin:$PATH"
fail=0
run() { name=$1; shift
  if out=$(timeout 120 "$@" 2>&1); then printf 'OK    %s\n' "$name"; else printf 'FAIL  %s\n%s\n' "$name" "$out" | tail -20; fail=1; fi
  if printf '%s' "$out" | grep -q '^FAIL'; then printf 'FAIL  %s (assert)\n' "$name"; fail=1; fi; }
python3 -m pyflakes $RX/*.py $TX/*.py | grep -v "imported but unused" && fail=1
cmp -s $RX/proto.py $TX/proto.py || { echo "FAIL  proto.py różni się między receiver/ i sender/"; fail=1; }
for t in test_tx test_drop test_tx2 test_bugs_tx test_shutdown test_resilience_tx h_rate; do run $t python3 $T/$t.py $TX; done
for t in test_rx2 test_bugs test_resilience test_startup; do run $t python3 $T/$t.py $RX; done
run test_e2e python3 $T/test_e2e.py $RX $TX
python3 -c "import playwright" 2>/dev/null && { run test_web python3 $T/test_web.py $RX; run test_web2 python3 $T/test_web2.py $RX; } || echo "SKIP  test_web* (brak playwright)"
command -v xvfb-run >/dev/null && python3 -c "import tkinter" 2>/dev/null && run test_gui xvfb-run -a python3 $T/test_gui.py $RX || echo "SKIP  test_gui (brak tkinter / xvfb-run)"
rm -f "$BTLOG"
[ $fail -eq 0 ] && echo "WSZYSTKO OK" || { echo "SĄ BŁĘDY"; exit 1; }
