# DualSense bridge

[English](README.md) | **Polski**

DualSense bridge przesyła dane z pada PS5 (DualSense) do komputera z Windows przez sieć lokalną. Na PC pad działa
jak zwykły DualSense (z HIDMaestro) albo pad PS4 (z ViGEmBus): przyciski, gałki, żyroskop, touchpad, a w drugą
stronę wibracje i lightbar.

Pad łączy się przez Bluetooth z **nadajnikiem**, czyli małym urządzeniem z Linuksem w pobliżu, np. Raspberry Pi albo
starym telefonem z postmarketOS. Nadajnik od razu wysyła do PC każdą zmianę stanu pada. Dzięki temu PC może stać
w innym pokoju, a grasz z kanapy przed telewizorem.

Na PC jest okno do sterowania wszystkim, także samym padem:
- przełączenie obrazu i dźwięku na TV jednym skrótem (tryb konsoli),
- remapowanie przycisków, gałek i żyroskopu, z profilami per pad i per gra,
- touchpad jako mysz,
- parowanie padów z nadajnikiem bez podchodzenia do niego,
- pad master, któremu wolno więcej (np. gdy grają dzieci).

![Okno receivera: pady z baterią i opóźnieniem, rysunek pada do remapowania](docs/img/main.png)

Więcej zrzutów: [docs/FUNKCJE.pl.md](docs/FUNKCJE.pl.md#zrzuty-ekranu).

```
DualSense ──Bluetooth──> nadajnik (sender/) ──sieć, UDP 5005──> PC (receiver/) ──> wirtualny DualSense / DS4 ──> gry
                                                                       └──> serwer DSU (gyro dla emulatorów)
```

## Czego potrzebujesz

| | PC | Nadajnik |
|---|---|---|
| Sprzęt | komputer z Windows 10 / 11 | Raspberry Pi, stary telefon z postmarketOS albo inny komputer z Linuksem i Bluetooth |
| Oprogramowanie | [Python 3.11+](https://www.python.org/downloads/), [Git for Windows](https://git-scm.com/download/win), [.NET 10](https://dotnet.microsoft.com/download/dotnet/10.0) (dla HIDMaestro) | Python 3.11+, `git`, BlueZ, systemd, jądro ze sterownikiem `hid-playstation` |
| Sieć | ta sama sieć lokalna co nadajnik | najlepiej kabel (Ethernet); Wi-Fi działa, ale daje skoki opóźnienia |
| Bluetooth | - | wbudowany wystarczy; dongle USB (np. TP-Link UB500) często ma wyraźnie mniejsze opóźnienie - nadajnik wybiera go sam |

## Instalacja

### 1. PC (Windows)

W PowerShellu albo wierszu poleceń:

```
git clone https://github.com/matt400/dualsense-bridge
cd dualsense-bridge\receiver
pip install -r requirements.txt
python receiver_controller.py --autostart on
python receiver_controller.py
```

- **Wirtualne pady:** [HIDMaestro](https://github.com/hifihedgehog/HIDMaestro) (natywny DualSense, rozwijany) receiver
  pobiera i aktualizuje sam (~56 MB, chwilę po pierwszym starcie). Receiver uruchamia się **jako administrator**
  (HIDMaestro tworzy pady tylko tak). Okno UAC pojawia się tylko przy pierwszym starcie: potem receiver startuje przez
  zadanie Harmonogramu z najwyższymi uprawnieniami (`DualSenseBridgeAdmin`, autostart tak samo). `--no-elevate`
  wyłącza samo podnoszenie uprawnień.
- Zapas: instalator `vgamepad` zaproponuje sterownik **ViGEmBus** (pad PS4). Zainstaluj go. Bez HIDMaestro albo bez
  uprawnień administratora receiver użyje ViGEmBus. Wybór: Ustawienia → Pady.
- Przy pierwszym starcie zapora Windows zapyta o dostęp do sieci. Zezwól w **sieciach prywatnych** (UDP 5005).
- `--autostart on` uruchamia receiver zaraz po zalogowaniu do Windows (`--autostart off` wyłącza).
- Receiver działa w zasobniku. Kliknij jego ikonę, żeby otworzyć okno.
- Zapisz adres IP komputera (`ipconfig` → „Adres IPv4”, np. `192.168.1.10`). Będzie potrzebny nadajnikowi.

### 2. Nadajnik (Linux)

Raspberry Pi OS / Debian / Ubuntu:

```
sudo apt install python3 python3-evdev bluez git
git clone https://github.com/matt400/dualsense-bridge
cd dualsense-bridge/sender
cp sender.example.toml sender.toml
nano sender.toml                        # odkomentuj i ustaw: host = "IP komputera"
sudo ./install-service.sh
```

postmarketOS / Alpine: `doas apk add python3 py3-evdev bluez git`, a w pozostałych poleceniach `doas` zamiast `sudo`.

Nadajnik działa jako usługa systemd i startuje sam po włączeniu urządzenia. Log: `journalctl -u dualsense-sender -f`.
Usunięcie usługi: `sudo ./install-service.sh --remove`.

### 3. Pad

1. W oknie na PC kliknij ikonę **Bluetooth** w nagłówku, potem **Skanuj**.
2. Na padzie przytrzymaj **Create + PS**, aż lightbar zacznie szybko migać.
3. Przy padzie na liście kliknij **Sparuj**.

Potem wystarczy włączyć pada przyciskiem PS, a połączy się sam. Pojawi się w oknie i w grach jako DualSense (HIDMaestro)
albo pad PS4 (ViGEmBus). Kolejność graczy jest stała: pad 2 nie zostanie graczem 1, nawet gdy włączysz go pierwszy.

## Pierwsze kroki

- **Skróty pada:** ikona pada w nagłówku okna pokazuje wszystkie skróty (np. PS + Share = zrzut ekranu).
- **Tryb konsoli** (obraz i dźwięk na TV): w Ustawieniach → Tryb konsoli wybierz urządzenia audio TV i PC. Potem
  dwa naciśnięcia R2 na padzie przełączają ekrany.
- **Remapowanie:** kliknij przycisk albo gałkę na rysunku pada w oknie. **Zapisz** zapisuje profil.
- **Pełny ekran** (F11 albo ikona) to tryb TV: większe okno sterowane padem.

## Aktualizacja

Przycisk **Aktualizuj** w oknie pobiera nową wersję na PC i na nadajniku (`git pull`) i uruchamia obie strony od nowa.
Ustawienia i profile zostają, a receiver robi ich kopię (Ustawienia → Kopie).

## Problemy

Najczęstsze:
- **Pad nie pojawia się w grach:** Ustawienia → Pady pokazują, czego brakuje (HIDMaestro, uprawnień administratora
  albo ViGEmBus).
- **„Nadajnik offline” w oknie:** zły `host` w `sender.toml`, zapora Windows (UDP 5005) albo nadajnik w innej sieci.
- **Skoki opóźnienia:** nadajnik na Wi-Fi. Podłącz go kablem.

W sieci dzielonej z innymi ustaw ten sam `key` po obu stronach: [docs/BEZPIECZENSTWO.pl.md](docs/BEZPIECZENSTWO.pl.md).

Pełna lista: [docs/PROBLEMY.pl.md](docs/PROBLEMY.pl.md).

## Więcej

- [docs/FUNKCJE.pl.md](docs/FUNKCJE.pl.md) – wszystkie funkcje okna, trybu konsoli, pada master, Wake-on-LAN i widoku Test
- [docs/KONFIGURACJA.pl.md](docs/KONFIGURACJA.pl.md) – pliki ustawień (`sender.toml`, `receiver.toml`, `console.json`) i aktualizacja
- [docs/BEZPIECZENSTWO.pl.md](docs/BEZPIECZENSTWO.pl.md) – klucz HMAC, ochrona przed replay, co działa z jakimi uprawnieniami
- [docs/ROZWOJ.pl.md](docs/ROZWOJ.pl.md) – struktura kodu i testy

## Licencja

[MIT](LICENSE)
