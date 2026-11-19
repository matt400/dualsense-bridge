#!/bin/sh
# Tests on Linux (stubs for Windows and the evdev devices): ./tests/run_tests.sh   (from the repo root)   JOBS=N: N tests at once (default: the CPU count, JOBS=1 = one by one)
# Requirements: python3 >= 3.11, pip install evdev ruff mypy (or pyflakes); the window: pip install PySide6-Essentials (offscreen, no Xvfb).
set -u
cd "$(dirname "$0")/.."
T=tests; RX=receiver; TX=sender; CM=common
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"      # the window tests need no display
export PATH="$PWD/$T/fakebin:$PATH"
fail=0
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 1)}"      # tests run in parallel (each is its own process with its own temp dir)
OUT=$(mktemp -d)
# run NAME CMD...: queue one test; the queue runs after the lint / type checks, the results print in queue order
run() { printf '%s\n' "$*" >> "$OUT/jobs"; }
if command -v ruff >/dev/null 2>&1; then ruff check --quiet $RX $TX $CM $T || fail=1      # rules in pyproject.toml
else python3 -m pyflakes $RX/*.py $TX/*.py $CM/*.py | grep -v "imported but unused" && fail=1; fi
# types: the packages import each other by flat name, so each is checked from its own directory (the receiver as on Windows)
if python3 -m mypy --version >/dev/null 2>&1; then      # python3 -m: the same interpreter (and packages) as the tests
  (cd $RX && python3 -m mypy --config-file ../pyproject.toml --platform win32 --no-error-summary .) || fail=1
  (cd $TX && python3 -m mypy --config-file ../pyproject.toml --no-error-summary .) || fail=1
fi
if [ -e $RX/proto.py ] || [ -e $TX/proto.py ]; then echo "FAIL  proto.py belongs only in $CM/ (no copies in receiver/ or sender/)"; fail=1; fi
# The tests are found by name, so a new file runs without being listed here:
#   test_*_tx.py  -> the sender           test_*_gui.py -> the window (only with PySide6)
#   BOTH below    -> receiver and sender  every other test_*.py -> the receiver
# test_proto_golden.py runs once per side (both import the same proto, from their own sys.path).
BOTH="test_e2e test_e2e_key test_bt"
if python3 -c "import PySide6.QtWidgets, PySide6.QtTest" 2>/dev/null; then gui=1; else gui=0; echo "SKIP  *_gui tests (no PySide6)"; fi
for f in $T/test_*.py; do
  t=$(basename "$f" .py)
  case " $BOTH " in *" $t "*) run $t python3 $f $RX $TX; continue;; esac
  case "$t" in
    *_tx) run $t python3 $f $TX;;
    *_gui) [ $gui -eq 1 ] && run $t python3 $f $RX;;
    *) run $t python3 $f $RX;;
  esac
done
run test_proto_golden_tx python3 $T/test_proto_golden.py $TX
# one job: its own BTLOG (the fake bluetoothctl's call log), output kept in a file per test
xargs -P "$JOBS" -L 1 sh -c '
  name=$1; shift; res="$0/res.$name"
  export BTLOG="$(mktemp)"
  if out=$(timeout 120 "$@" 2>&1); then printf "OK    %s\n" "$name" > "$res"; else printf "FAIL  %s\n%s\n" "$name" "$(printf "%s\n" "$out" | tail -20)" > "$res"; fi
  if printf "%s" "$out" | grep -q "^FAIL"; then printf "FAIL  %s (assert)\n" "$name" >> "$res"; fi
  rm -f "$BTLOG"' "$OUT" < "$OUT/jobs"
while read -r name _rest; do
  cat "$OUT/res.$name" 2>/dev/null || { printf 'FAIL  %s (no result)\n' "$name"; fail=1; }
  grep -q '^FAIL' "$OUT/res.$name" 2>/dev/null && fail=1
done < "$OUT/jobs"
rm -rf "$OUT"
[ $fail -eq 0 ] && echo "ALL OK" || { echo "FAILURES"; exit 1; }
