# DualSense bridge – instrukcje dla Claude

Pad DualSense → Bluetooth → **sender** (Linux, evdev) → UDP 5005 → **receiver** (Windows) → wirtualny DualSense
(HIDMaestro) albo DS4 (ViGEmBus) + serwer DSU (127.0.0.1:26760). Feedback (wibracja, lightbar, spusty) idzie z powrotem.

## Stack
- Python 3.11+ (CI: 3.12), bez frameworka, bez buildu.
- Sender: `evdev`, asyncio, BlueZ (`busctl` / `bluetoothctl`), systemd; działa też na musl (postmarketOS, `doas`).
- Receiver: PySide6 (okno + tray), `pythonnet` + HIDMaestro (.NET 10), `vgamepad` (ViGEm), `pycaw`, `winotify`, ctypes WinAPI.

## Struktura
```
sender/    sender_controller.py (wejście) + sender_*.py; install-service.sh, service/
receiver/  receiver_controller.py (wejście) + rx_*.py (logika) + rx_qt*.py (okno)
tests/     testy na Linuksie ze stubami Windows/evdev; fakebin/ = podstawiony bluetoothctl
docs/      EN domyślnie, PL jako *.pl.md
tools/     screenshots.py (zrzuty okna offscreen -> docs/img)
```

## Twarde zasady
- `receiver/proto.py` i `sender/proto.py` MUSZĄ być identyczne bajt w bajt (test to sprawdza). Zmiana = edytuj oba.
- Protokół jest wstecznie zgodny: nowe pola = dłuższy wariant tego samego typu, wysyłany tylko do strony,
  o której wiadomo, że go zna (wersja / CAP_* / pong2 / scfg). Starsza strona odrzuca nieznaną długość.
  Nie zmieniaj formatów istniejących pakietów.
- Gorąca ścieżka (odczyt evdev, pakowanie, odbiór UDP, budowa raportu HID) – bez alokacji i kopii, liczy się latencja.
- Zapis plików ustawień tylko przez `proto.write_file` / `proto.write_json` (atomowo, fsync).
- Teksty okna przez `rx_i18n` (PL + EN), nie wpisuj stringów UI na sztywno.
- Kod Windows (ctypes, pythonnet, COM) musi się importować na Linuksie ze stubami z `tests/rxenv.py`.

## Styl kodu
- Pliki: `sender_<obszar>.py`, `rx_<obszar>.py`, okno `rx_qt_<obszar>.py`; snake_case.
- Komentarze i docstringi po polsku; docstring modułu opisuje jego rolę (i w plikach wejściowych listę modułów).
- Receiver: współdzielony stan w `rx_state` (`S.xxx`, `S.lock`); długie pętle w wątkach przez `rx_state` (run_forever).
- Sender: asyncio; pętle owinięte w `forever()` (restart po wyjątku, nie zabijają procesu).
- Konfiguracja: `DEFAULTS` w `sender_config` / `rx_config`, plik TOML/JSON, flagi CLI nadpisują plik.
- Lint: `ruff` wg `pyproject.toml` (linia 140 znaków, py311).

## Testy
```
pip install evdev ruff PySide6-Essentials
./tests/run_tests.sh          # z katalogu głównego; ruff + wszystkie testy, GUI w Qt offscreen
```
- Testy to zwykłe skrypty (`python3 tests/test_x.py receiver|sender`), bez pytest.
  `*_tx.py` = sender, reszta = receiver, `test_e2e*` / `test_bt` = oba.
- Nowy plik testu dopisz do odpowiedniej listy w `tests/run_tests.sh`, inaczej nie wejdzie do CI.
- Regresje z przeglądu błędów: `tests/test_hunt<N>.py` / `test_hunt<N>_tx.py`.
- Ręcznie (nie da się w CI): prawdziwy pad, ViGEm, HIDMaestro, DisplaySwitch, pycaw, powiadomienia.

## Uruchamianie
- Receiver: `cd receiver && pip install -r requirements.txt && python receiver_controller.py` (sam podnosi się do admina; `--no-elevate`).
- Sender: `cd sender && cp sender.example.toml sender.toml` (ustaw `host`), `sudo ./install-service.sh`;
  log `journalctl -u dualsense-sender -f`.
- Po zmianach UI: `python3 tools/screenshots.py` i commit PNG z `docs/img`.

## Git
- Gałęzie: `fix/…`, `feat/…`, `docs/…`, `security/…`; zmiany przez PR z merge commitem do `main`.
- Commity: krótki opis po polsku z prefiksem obszaru (`Receiver: …`, `Sender BT: …`, `Bug hunt N: …`, `docs: …`).
- Dokumentacja: zmiana w EN (`docs/*.md`) wymaga odpowiednika w PL (`docs/*.pl.md`) i odwrotnie.
