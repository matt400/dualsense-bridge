#!/bin/sh
# Tests on Linux (stubs for Windows and the evdev devices): ./tests/run_tests.sh   (from the repo root)
# Requirements: python3 >= 3.11, pip install evdev ruff mypy (or pyflakes); the window: pip install PySide6-Essentials (offscreen, no Xvfb).
set -u
cd "$(dirname "$0")/.."
T=tests; RX=receiver; TX=sender; CM=common
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"      # the window tests need no display
export BTLOG="$(mktemp)" PATH="$PWD/$T/fakebin:$PATH"
fail=0
run() { name=$1; shift
  if out=$(timeout 120 "$@" 2>&1); then printf 'OK    %s\n' "$name"; else printf 'FAIL  %s\n' "$name"; printf '%s\n' "$out" | tail -20; fail=1; fi
  if printf '%s' "$out" | grep -q '^FAIL'; then printf 'FAIL  %s (assert)\n' "$name"; fail=1; fi; }
if command -v ruff >/dev/null 2>&1; then ruff check --quiet $RX $TX $CM $T || fail=1      # rules in pyproject.toml
else python3 -m pyflakes $RX/*.py $TX/*.py $CM/*.py | grep -v "imported but unused" && fail=1; fi
# types: the packages import each other by flat name, so each is checked from its own directory (the receiver as on Windows)
if python3 -m mypy --version >/dev/null 2>&1; then      # python3 -m: the same interpreter (and packages) as the tests
  (cd $RX && python3 -m mypy --config-file ../pyproject.toml --platform win32 --no-error-summary .) || fail=1
  (cd $TX && python3 -m mypy --config-file ../pyproject.toml --no-error-summary .) || fail=1
fi
if [ -e $RX/proto.py ] || [ -e $TX/proto.py ]; then echo "FAIL  proto.py ma być tylko w $CM/ (bez kopii w receiver/ i sender/)"; fail=1; fi
for t in test_touchpad_tx test_drop test_control_tx test_info_loop_tx test_shutdown test_resilience_tx test_slots_tx test_rumble_tx test_rumble_fw_tx test_trigfx_tx test_rt_tx test_devwatch_tx test_hidraw_write_retry_tx test_update_changed_files_tx test_lost_motion_device_tx test_single_report_packet_tx test_migrate_defaults_tx test_update_key_tx test_features_tx test_wol_tx test_calib_tx test_critical_path_tx test_hidraw_errors_tx test_bt_scan_lifecycle_tx test_bt_window_errors_tx test_atomic_writes_tx test_file_modes_tx test_bt_multi_adapter_tx test_bt_link_policy_tx test_pad_limit_tx test_gametrig_tx test_security_tx test_battery_tx test_hot_path_tx test_config_hardening_tx test_btlink_tx test_state_writes_tx test_hci_sockets_tx test_boot_and_restart_tx test_migrate_once_tx test_rumble_auto_fallback_tx test_install_files_tx test_pad_filter_tx test_touch_syn_dropped_tx test_calib_lost_stream_tx test_bt_trust_refresh_tx test_lightbar_async_tx test_rumble_worker_tx test_idle_power_off_tx test_start_errors_tx test_btlink_refusal_tx test_calib_device_vanishes_tx test_update_run_state_tx test_bt_loop_and_devwatch_tx test_scan_cancel_tx test_start_order_tx test_config_validation_tx test_btadapter_usb_path_tx test_late_devices_slots_tx test_haptics_tx test_haptics_stream_tx test_speaker_tx test_tick_tx test_stream_flush_tx test_remote_cfg_tx; do run $t python3 $T/$t.py $TX; done
for t in test_i18n test_receiver_core test_editor test_bugs test_resilience test_startup test_console test_master test_restart test_relaunch_job test_input_parsing_edges test_buttons_edges test_master_follow test_console_edges test_ds4_push test_window_edges test_update_vs_check test_features test_trigfx test_extras test_padtest test_access test_autostart test_critical_path test_release_on_exit test_touchpad_tv_mode test_vigem_callbacks test_input_state_boundaries test_atomic_writes test_hm_download_elevation test_lightbar_game_color test_loss_indicator_restart test_feedback_idle_loop test_net_loop_checks test_settings_update_races test_feedback_wakeups test_display test_vpad test_hm_update test_gametrig test_security test_gstick test_proto_fields test_proto_golden test_battery test_report_hardening_writes test_bt_busy_and_tv_retry test_tilt_settings_widgets test_thread_safety test_restart_and_trigger_guards test_v3_freshness test_hot_path test_swap test_diag test_haptics test_speaker test_hm_il test_sender_cfg; do run $t python3 $T/$t.py $RX; done
run test_e2e python3 $T/test_e2e.py $RX $TX
run test_e2e_key python3 $T/test_e2e_key.py $RX $TX
run test_bt python3 $T/test_bt.py $RX $TX
run test_proto_golden_tx python3 $T/test_proto_golden.py $TX
run test_service_tx python3 $T/test_service_tx.py $TX
run test_btadapter_tx python3 $T/test_btadapter_tx.py $TX
run test_blocking_calls_tx python3 $T/test_blocking_calls_tx.py $TX
run test_update python3 $T/test_update.py $RX
run test_update_tx python3 $T/test_update_tx.py $TX
python3 -c "import PySide6.QtWidgets, PySide6.QtTest" 2>/dev/null && { run test_gui python3 $T/test_gui.py $RX; run test_console_gui python3 $T/test_console_gui.py $RX; run test_settings_gui python3 $T/test_settings_gui.py $RX; run test_extras_gui python3 $T/test_extras_gui.py $RX; run test_padtest_gui python3 $T/test_padtest_gui.py $RX; run test_access_gui python3 $T/test_access_gui.py $RX; run test_gui_hot_path python3 $T/test_gui_hot_path.py $RX; run test_bt_gui python3 $T/test_bt_gui.py $RX; run test_keys_gui python3 $T/test_keys_gui.py $RX; run test_battery_gui python3 $T/test_battery_gui.py $RX; run test_pads_view_gui python3 $T/test_pads_view_gui.py $RX; run test_diag_gui python3 $T/test_diag_gui.py $RX; run test_sender_cfg_gui python3 $T/test_sender_cfg_gui.py $RX; run test_sidebar_fit_gui python3 $T/test_sidebar_fit_gui.py $RX; } || echo "SKIP  test_gui (brak PySide6)"
rm -f "$BTLOG"
[ $fail -eq 0 ] && echo "WSZYSTKO OK" || { echo "SĄ BŁĘDY"; exit 1; }
