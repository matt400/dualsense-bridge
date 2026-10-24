#!/bin/sh
# Tests on Linux (stubs for Windows and the evdev devices): ./tests/run_tests.sh   (from the repo root)
# Requirements: python3 >= 3.11, pip install evdev ruff mypy (or pyflakes); the window: pip install PySide6-Essentials (offscreen, no Xvfb).
set -u
cd "$(dirname "$0")/.."
T=tests; RX=receiver; TX=sender
export BTLOG="$(mktemp)" PATH="$PWD/$T/fakebin:$PATH"
fail=0
run() { name=$1; shift
  if out=$(timeout 120 "$@" 2>&1); then printf 'OK    %s\n' "$name"; else printf 'FAIL  %s\n%s\n' "$name" "$out" | tail -20; fail=1; fi
  if printf '%s' "$out" | grep -q '^FAIL'; then printf 'FAIL  %s (assert)\n' "$name"; fail=1; fi; }
if command -v ruff >/dev/null 2>&1; then ruff check --quiet $RX $TX $T || fail=1      # rules in pyproject.toml
else python3 -m pyflakes $RX/*.py $TX/*.py | grep -v "imported but unused" && fail=1; fi
# types: the packages import each other by flat name, so each is checked from its own directory (the receiver as on Windows)
if python3 -m mypy --version >/dev/null 2>&1; then      # python3 -m: the same interpreter (and packages) as the tests
  (cd $RX && python3 -m mypy --config-file ../pyproject.toml --platform win32 --no-error-summary .) || fail=1
  (cd $TX && python3 -m mypy --config-file ../pyproject.toml --no-error-summary .) || fail=1
fi
cmp -s $RX/proto.py $TX/proto.py || { echo "FAIL  proto.py różni się między receiver/ i sender/"; fail=1; }
for t in test_tx test_drop test_tx2 test_bugs_tx test_shutdown test_resilience_tx test_slots_tx test_rumble_tx test_rumble_fw_tx test_trigfx_tx test_rt_tx test_devwatch_tx test_review_tx test_update_key_tx test_features_tx test_wol_tx test_calib_tx test_critical_path_tx test_hidraw_errors_tx test_bt_scan_lifecycle_tx test_bt_window_errors_tx test_atomic_writes_tx test_file_modes_tx test_bt_multi_adapter_tx test_bt_link_policy_tx test_pad_limit_tx test_gametrig_tx test_security_tx test_battery_tx test_hot_path_tx test_config_hardening_tx h_rate; do run $t python3 $T/$t.py $TX; done
for t in test_i18n test_rx2 test_editor test_bugs test_resilience test_startup test_console test_master test_restart test_relaunch_job test_review test_features test_trigfx test_extras test_padtest test_access test_autostart test_critical_path test_release_on_exit test_touchpad_tv_mode test_vigem_callbacks test_input_state_boundaries test_atomic_writes test_hm_download_elevation test_lightbar_game_color test_loss_indicator_restart test_feedback_idle_loop test_review_fixes test_display test_vpad test_hm_update test_gametrig test_security test_gstick test_proto_fields test_proto_golden test_battery test_report_hardening_writes test_review_followups test_hot_path; do run $t python3 $T/$t.py $RX; done
run test_e2e python3 $T/test_e2e.py $RX $TX
run test_e2e_key python3 $T/test_e2e_key.py $RX $TX
run test_bt python3 $T/test_bt.py $RX $TX
run test_proto_golden_tx python3 $T/test_proto_golden.py $TX
run test_service_tx python3 $T/test_service_tx.py $TX
run test_btadapter_tx python3 $T/test_btadapter_tx.py $TX
run test_blocking_calls_tx python3 $T/test_blocking_calls_tx.py $TX
run test_update python3 $T/test_update.py $RX
run test_update_tx python3 $T/test_update_tx.py $TX
python3 -c "import PySide6.QtWidgets, PySide6.QtTest" 2>/dev/null && { run test_gui python3 $T/test_gui.py $RX; run test_console_gui python3 $T/test_console_gui.py $RX; run test_settings_gui python3 $T/test_settings_gui.py $RX; run test_extras_gui python3 $T/test_extras_gui.py $RX; run test_padtest_gui python3 $T/test_padtest_gui.py $RX; run test_access_gui python3 $T/test_access_gui.py $RX; run test_gui_hot_path python3 $T/test_gui_hot_path.py $RX; run test_bt_gui python3 $T/test_bt_gui.py $RX; run test_keys_gui python3 $T/test_keys_gui.py $RX; run test_battery_gui python3 $T/test_battery_gui.py $RX; } || echo "SKIP  test_gui (brak PySide6)"
rm -f "$BTLOG"
[ $fail -eq 0 ] && echo "WSZYSTKO OK" || { echo "SĄ BŁĘDY"; exit 1; }
