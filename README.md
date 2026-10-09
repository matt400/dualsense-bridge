# DualSense bridge

Pad PS5 (DualSense) podłączony przez Bluetooth do telefonu z Linuksem (np. OnePlus 6 z postmarketOS)
działa na komputerze z Windows jak pad PS4. Stan pada idzie przez sieć lokalną (UDP).

```
DualSense ──Bluetooth──> telefon: sender/ ──UDP 5005──> PC: receiver/ ──> wirtualny DS4 (ViGEm) ──> gry
                                                          └──> serwer DSU 26760 (gyro dla emulatorów)
```

## Funkcje

- **Wirtualny DS4** (ViGEm) z przyciskami, gałkami, spustami, touchpadem, żyroskopem, wibracjami i kolorem lightbara.
- **Serwer DSU (Cemuhook)** z ruchem i dotykiem dla emulatorów (Cemu, Yuzu/Ryujinx, Dolphin).
- **Touchpad jako mysz:** kursor, tap = lewy przycisk, przewijanie dwoma palcami, tap dwoma palcami = prawy przycisk.
  Przełączanie: PS + klik touchpada.
- **Edytor w przeglądarce** (przycisk „Remap” w oknie receivera, rysunek pada):
  - remapowanie przycisków i gałek,
  - strefy martwe, krzywe i anti-deadzone gałek i spustów,
  - celowanie żyroskopem jako prawa gałka albo mysz,
  - profile per gra (`gra.exe`), które włączają się same, gdy gra jest aktywnym oknem.
- **Skróty z PS:**

  | Skrót | Działanie |
  |---|---|
  | PS + Share | zrzut ekranu |
  | PS + d-pad góra / dół | czułość myszy |
  | PS + d-pad lewo / prawo | zmiana profilu |
  | długie PS | Xbox Game Bar albo Steam Big Picture |

- **Okno receivera** (ikona w zasobniku): bateria, ping, utrata pakietów, realne Hz pada, aktywny profil, przycisk wyłączenia pada.
- **Wyłączanie pada** z PC albo samo po bezczynności (domyślnie 15 min).
- **Niskie opóźnienia:**
  - przyciski wysyłane natychmiast, z powtórkami na wypadek zgubionego pakietu,
  - gałki i ruch zlewane do 250 Hz i uśredniane,
  - priorytety wątków i procesów, timer 1 ms na Windows.

## Wymagania

| | Receiver (PC) | Sender (telefon / Linux) |
|---|---|---|
| System | Windows 10 / 11 | Linux z jądrem ze sterownikiem `hid-playstation` |
| Python | 3.11+ | 3.11+ |
| Inne | sterownik ViGEmBus, `vgamepad` | `evdev`, `bluez` (`bluetoothctl`) |

## Instalacja

### Receiver (Windows)

```
cd receiver
pip install -r requirements.txt
```

Pakiet `vgamepad` przy instalacji proponuje instalację sterownika ViGEmBus, więc go zainstaluj. Przy pierwszym
uruchomieniu zezwól Pythonowi na ruch w sieci prywatnej w zaporze Windows (UDP 5005).

### Sender (postmarketOS / Alpine)

```
doas apk add python3 py3-evdev bluez
```

Na innych dystrybucjach: `pip install -r sender/requirements.txt` oraz `bluez` z menedżera pakietów.

Sparuj pada (przytrzymaj PS + Create, aż lightbar zacznie migać):

```
bluetoothctl
  scan on
  pair XX:XX:XX:XX:XX:XX
  trust XX:XX:XX:XX:XX:XX
  connect XX:XX:XX:XX:XX:XX
```

## Uruchomienie

PC (najpierw):

```
cd receiver
python receiver_controller.py
```

Telefon (podaj IP komputera):

```
cd sender
doas python3 sender_controller.py 192.168.1.4 --stats
```

`--stats` co 5 s wypisuje ping i realną częstotliwość raportów pada. Autostart receivera z Windows:
`python receiver_controller.py --autostart on`.

## Konfiguracja

Wszystkie ustawienia z wartościami domyślnymi i opisem są w `sender/sender.example.toml` i
`receiver/receiver.example.toml`. Skopiuj plik bez `.example`, odkomentuj potrzebne linie i uruchom z
`--config sender.toml` / `--config receiver.toml`. Pliki `*.toml` są w `.gitignore`, bo mogą zawierać klucz.

Najczęściej zmieniane:

| Ustawienie | Gdzie | Domyślnie | Co robi |
|---|---|---|---|
| `host` | sender | 192.168.1.4 | IP komputera z receiverem |
| `pad_min_dt` | sender | 0.004 | min. odstęp pakietów stanu (4 ms = 250 Hz) |
| `idle_off_min` | sender | 15 | wyłącz pada po tylu minutach bez użycia, 0 = nigdy |
| `key` | oba | "" | wspólny klucz HMAC (opcjonalne uwierzytelnianie pakietów) |
| `mouse_sens`, `scroll_sens` | receiver | 0.5, 0.6 | czułość myszy i przewijania touchpadem |
| `ps_modifier` | receiver | true | PS jako klawisz skrótów (PS idzie do gry po puszczeniu) |
| `ps_hold_action` | receiver | gamebar | długie PS: `gamebar`, `bigpicture`, `none` |
| `game_profiles` | receiver | true | automatyczny profil wg aktywnego okna |

Profile remapowania zapisują się w `receiver/remap.json`, przypisane do pada (MAC), a nie do slotu.

## Rozwiązywanie problemów

| Objaw | Co sprawdzić |
|---|---|
| `WARNING: no reply from receiver` na telefonie | IP komputera, zapora Windows (UDP 5005), czy `key` jest taki sam po obu stronach |
| Wysoki ping / skoki w oknie receivera | Wi-Fi na telefonie; najlepiej Ethernet przez hub USB, wtedy wyłącz Wi-Fi (`nmcli radio wifi off`) |
| Gyro kręci w złą stronę | edytor → Gyro aim → „Invert horizontal / vertical” |
| Ruch odwrócony w emulatorze (DSU) | `acc_sign` / `gyro_sign` w konfiguracji receivera |
| Profil gry się nie włącza | gry z anti-cheatem mogą blokować odczyt nazwy procesu; przełącz ręcznie PS + d-pad |
| `virtual DS4 error ... ViGEmBus` | zainstaluj sterownik ViGEmBus |

## Struktura

```
receiver/   Windows: receiver_controller.py (start), rx_*.py (moduły), rx_remap_ui.html (edytor)
sender/     Linux:   sender_controller.py (start), sender_*.py (moduły)
tests/      testy na Linuksie z zaślepkami Windows i urządzeń evdev
```

`proto.py` (format pakietów) musi być identyczny w `receiver/` i `sender/`. Testy to sprawdzają.

## Testy

```
pip install evdev pyflakes playwright      # playwright: test edytora w przeglądarce (opcjonalnie)
./tests/run_tests.sh
```

Testy obejmują:
- logikę obu stron: touchpad, gesty, limiter, przepełnienie bufora, skróty, profile, gyro, kształtowanie gałek;
- komunikację end-to-end przez UDP;
- start prawdziwych procesów;
- edytor w headless Chromium i okno receivera pod Xvfb.

Działanie z prawdziwym padem, ViGEm i Windows trzeba sprawdzić ręcznie.
