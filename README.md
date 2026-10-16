# DualSense bridge

Pad PS5 (DualSense) podłączony przez Bluetooth do telefonu z Linuksem działa na PC z Windows jak pad PS4.
Telefon stoi przy telewizorze, PC może być daleko: stan pada idzie siecią lokalną (UDP).

```
DualSense ──Bluetooth──> telefon: sender/ ──UDP 5005──> PC: receiver/ ──> wirtualny DS4 (ViGEm) ──> gry
                                                          └──> serwer DSU 26760 (gyro dla emulatorów)
```

## Jak to działa

- **Sender** (telefon, np. OnePlus 6 z postmarketOS) czyta pady przez `evdev` i wysyła ich stan od razu po każdej
  zmianie (do 1000 Hz). Przyciski idą z powtórkami na wypadek zgubionego pakietu. Działa jako usługa systemd (root).
- **Receiver** (PC) tworzy wirtualnego DS4 dla każdego podłączonego pada i serwer DSU dla emulatorów. Odsyła do pada
  wibracje, kolor lightbara i efekty spustów.
- **Okno receivera** (PySide6, ikona w zasobniku) pokazuje stan padów i opóźnienie pad → gra. Z okna remapujesz
  przyciski i gałki (profile per pad i per gra), ustawiasz tryb konsoli i pad master oraz zarządzasz Bluetooth telefonu.
- **Tryb konsoli:** dwa naciśnięcia R2 przełączają obraz i dźwięk między monitorem a TV. Włącza się sam po połączeniu
  pada master.
- **Skróty pada:** pełna lista jest w oknie (ikona pada w nagłówku) i zawsze zgadza się z bieżącymi ustawieniami.

## Wymagania

| | Receiver (PC) | Sender (telefon / Linux) |
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

### Telefon (postmarketOS / Alpine)

```
doas apk add python3 py3-evdev bluez git
git clone https://github.com/matt400/dualsense-bridge
cd dualsense-bridge/sender
cp sender.example.toml sender.toml      # host = IP komputera
doas ./install-service.sh
./setup-update-key.sh                   # klucz do aktualizacji z okna (repo prywatne)
```

Log nadajnika: `journalctl -u dualsense-sender -f`.

### Pad

Pada parujesz z okna receivera: ikona Bluetooth → **Skanuj** → **Sparuj**. Na padzie przytrzymaj Create + PS, aż
lightbar zacznie szybko migać.

## Aktualizacja

Przycisk **Aktualizuj** w oknie robi `git pull` na PC i na telefonie, po czym uruchamia obie strony od nowa.
Ustawienia i profile zostają, a receiver robi ich kopię (Ustawienia → Kopie).

## Więcej

- [docs/FUNKCJE.md](docs/FUNKCJE.md) – wszystkie funkcje okna, trybu konsoli, pada master, Wake-on-LAN i widoku Test
- [docs/KONFIGURACJA.md](docs/KONFIGURACJA.md) – pliki `*.toml` i `console.json` oraz szczegóły aktualizacji
- [docs/PROBLEMY.md](docs/PROBLEMY.md) – objaw → co sprawdzić
- [docs/ROZWOJ.md](docs/ROZWOJ.md) – struktura kodu i testy (`./tests/run_tests.sh`)
