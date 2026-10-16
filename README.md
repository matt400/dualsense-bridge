# DualSense bridge

Granie padem PS5 (DualSense) na kanapie przed telewizorem, gdy komputer z grami stoi daleko, np. w innym pokoju.

Bluetooth ma zasięg kilku metrów i przez ściany się rwie, więc pad nie łączy się z PC bezpośrednio. Łączy się
z małym **nadajnikiem** z Linuksem przy telewizorze: Raspberry Pi, starym telefonem z postmarketOS (np. OnePlus 6)
albo dowolnym komputerkiem z Bluetooth. Nadajnik przesyła stan pada siecią lokalną do PC. Na PC gry widzą zwykły pad
PS4 (DS4), a obraz idzie na telewizor.

Do tego jedno okno na PC do sterowania wszystkim, także samym padem z kanapy:
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
