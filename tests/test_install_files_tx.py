"""Sender: the install files, the unit and the example config are in English.
    python3 tests/test_install_files_tx.py sender"""
import os
import re
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- 10: install files, the unit and the example config are English ----------
root = os.path.dirname(os.path.abspath(sys.argv[1]))
polish = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]|\b(Brak|Klucz|klucz|Uruchomienie|prawdziwa|nie jest|sklonuj|skopiuj|widocznym)\b")
for rel in ("sender/install-service.sh", "sender/setup-update-key.sh", "sender/service/dualsense-sender.service", "sender/sender.example.toml"):
    bad = [ln.strip() for ln in open(os.path.join(root, rel), encoding="utf-8") if polish.search(ln)]
    check(not bad, f"{rel}: no Polish text {bad}")

sys.exit(1 if fails else 0)
