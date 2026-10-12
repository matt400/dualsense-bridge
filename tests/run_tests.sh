#!/bin/sh
# Testy na Linuksie (zaślepki Windows i urządzeń evdev): ./tests/run_tests.sh   (z katalogu głównego repo)
# Wymagania: python3 >= 3.11, pip install evdev ruff (albo pyflakes); okno: pip install PySide6-Essentials (tryb offscreen, bez Xvfb);
set -u
cd "$(dirname "$0")/.."
T=tests; RX=receiver; TX=sender
export BTLOG="$(mktemp)" PATH="$PWD/$T/fakebin:$PATH"
fail=0
run() { name=$1; shift
  if out=$(timeout 120 "$@" 2>&1); then printf 'OK    %s\n' "$name"; else printf 'FAIL  %s\n%s\n' "$name" "$out" | tail -20; fail=1; fi
  if printf '%s' "$out" | grep -q '^FAIL'; then printf 'FAIL  %s (assert)\n' "$name"; fail=1; fi; }
if command -v ruff >/dev/null 2>&1; then ruff check --quiet $RX $TX $T || fail=1      # reguły w pyproject.toml
else python3 -m pyflakes $RX/*.py $TX/*.py | grep -v "imported but unused" && fail=1; fi
cmp -s $RX/proto.py $TX/proto.py || { echo "FAIL  proto.py różni się między receiver/ i sender/"; fail=1; }
for t in test_tx test_drop test_tx2 test_bugs_tx test_shutdown test_resilience_tx test_slots_tx test_rumble_tx test_rt_tx test_review_tx test_update_key_tx test_features_tx h_rate; do run $t python3 $T/$t.py $TX; done
for t in test_i18n test_rx2 test_editor test_bugs test_resilience test_startup test_console test_master test_restart test_review test_features; do run $t python3 $T/$t.py $RX; done
run test_e2e python3 $T/test_e2e.py $RX $TX
run test_service_tx python3 $T/test_service_tx.py $TX
run test_update python3 $T/test_update.py $RX
run test_update_tx python3 $T/test_update_tx.py $TX
python3 -c "import PySide6.QtWidgets, PySide6.QtTest" 2>/dev/null && { run test_gui python3 $T/test_gui.py $RX; run test_console_gui python3 $T/test_console_gui.py $RX; run test_settings_gui python3 $T/test_settings_gui.py $RX; } || echo "SKIP  test_gui (brak PySide6)"
rm -f "$BTLOG"
[ $fail -eq 0 ] && echo "WSZYSTKO OK" || { echo "SĄ BŁĘDY"; exit 1; }
