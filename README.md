# DualSense bridge

DualSense bridge przesyła dane z pada PS5 (DualSense) do komputera z Windows przez sieć lokalną. Na PC pad działa
jak zwykły pad PS4: przyciski, gałki, żyroskop, touchpad, a w drugą stronę wibracje i lightbar.

Pad łączy się przez Bluetooth z **nadajnikiem**, czyli małym urządzeniem z Linuksem w pobliżu, np. Raspberry Pi albo
starym telefonem z postmarketOS (OnePlus 6). Nadajnik od razu wysyła do PC każdą zmianę stanu pada.
Dzięki temu PC może stać w innym pokoju, a grasz z kanapy przed telewizorem.

Na PC jest okno do sterowania wszystkim, także samym padem:
- przełączenie obrazu i dźwięku na TV jednym skrótem (tryb konsoli),
- remapowanie przycisków, gałek i żyroskopu, z profilami per pad i per gra,
- touchpad jako mysz,
- parowanie padów z nadajnikiem bez podchodzenia do niego,
- pad master z uprawnieniami, np. gdy grają dzieci.

```
DualSense ──Bluetooth──> nadajnik (sender/) ──sieć, UDP 5005──> PC (receiver/) ──> wirtualny DS4 ──> gry
                                                                       └──> serwer DSU (gyro dla emulatorów)
```

## Jak to działa

- **Nadajnik** (`sender/`, Linux przy TV) czyta pady przez `evdev` i wysyła ich stan od razu po każdej zmianie
  (do 1000 Hz). Przyciski idą z powtórkami na wypadek zgubionego pakietu. Działa jako usługa systemd (root).
- **Receiver** (`receiver/`, Windows) tworzy wirtualnego DS4 (ViGEm) dla każdego podłączonego pada i serwer DSU dla
  emulatorów. Odsyła do pada wibracje, kolor lightbara i efekty spustów.
- **Okno receivera** (PySide6, ikona w zasobniku) pokazuje stan padów, baterię i opóźnienie pad → gra. Na pełnym
  ekranie obsługuje się je padem.
- **Tryb konsoli:** dwa naciśnięcia R2 przełączają obraz i dźwięk między monitorem a TV. Włącza się sam po połączeniu
  pada master.
- **Skróty pada:** pełna lista jest w oknie (ikona pada w nagłówku) i zawsze zgadza się z bieżącymi ustawieniami.

## Wymagania

| | Receiver (PC) | Nadajnik (Raspberry Pi, telefon, inny Linux) |
|---|---|---|
| System | Windows 10 / 11 | Linux ze sterownikiem `hid-playstation` |
| Python | 3.11+ | 3.11+ |
| Inne | ViGEmBus, Git for Windows | `evdev`, `bluez`, systemd (`busctl`), `git` |

## Instalacja

### PC (Windows)

```
git clone https://github.com/matt400/dualsense-bridge
cd dualsense-bridge\receiver
pip install -r requirements.txt
python receiver_controller.py --autostart on
python receiver_controller.py
```

- Przy instalacji `vgamepad` zainstaluj sterownik ViGEmBus.
- W zaporze Windows zezwól Pythonowi na sieć prywatną (UDP 5005).
- `--autostart on` dodaje zadanie Harmonogramu zadań, które uruchamia receiver zaraz po zalogowaniu.

### Nadajnik (przykład: postmarketOS / Alpine)

```
doas apk add python3 py3-evdev bluez git
git clone https://github.com/matt400/dualsense-bridge
cd dualsense-bridge/sender
cp sender.example.toml sender.toml      # host = IP komputera
doas ./install-service.sh
./setup-update-key.sh                   # klucz do aktualizacji z okna (repo prywatne)
```

Raspberry Pi OS / Debian: `sudo apt install python3-evdev bluez git` zamiast `apk add`, `sudo` zamiast `doas`.
Log nadajnika: `journalctl -u dualsense-sender -f`.

### Pad

Pada parujesz z okna receivera: ikona Bluetooth → **Skanuj** → **Sparuj**. Na padzie przytrzymaj Create + PS, aż
lightbar zacznie szybko migać.

## Aktualizacja

Przycisk **Aktualizuj** w oknie robi `git pull` na PC i na nadajniku, po czym uruchamia obie strony od nowa.
Ustawienia i profile zostają, a receiver robi ich kopię (Ustawienia → Kopie).

## Więcej

- [docs/FUNKCJE.md](docs/FUNKCJE.md) – wszystkie funkcje okna, trybu konsoli, pada master, Wake-on-LAN i widoku Test
- [docs/KONFIGURACJA.md](docs/KONFIGURACJA.md) – pliki `*.toml` i `console.json` oraz szczegóły aktualizacji
- [docs/PROBLEMY.md](docs/PROBLEMY.md) – objaw → co sprawdzić
- [docs/ROZWOJ.md](docs/ROZWOJ.md) – struktura kodu i testy (`./tests/run_tests.sh`)
