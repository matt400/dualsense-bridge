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
  Przełączanie: PS + klik touchpada. Fizyczny klik nie przesuwa kursora: ruch z ostatnich ~80 ms przed kliknięciem
  jest cofany, przez ~150 ms po wciśnięciu i puszczeniu kursor stoi, a przeciąganie z wciśniętym klikiem zaczyna się
  dopiero po wyraźnym ruchu palca. Czułość i te czasy: Ustawienia → Mysz (działa od razu).
- **Jedno okno do wszystkiego** (PySide6, ikona w zasobniku):
  - po lewej karty padów: bateria, ping, utrata pakietów, realne Hz, aktywny profil, wyłączenie pada, korona mastera;
  - na środku rysunek pada na żywo: kliknij przycisk, spust albo gałkę, żeby ją zremapować lub dostroić;
  - po prawej profil (domyślny / per gra), zakładki **Selected**, **Gyro**, **Changes** i przycisk **Save** (Ctrl+S);
  - remapowanie przycisków i gałek, strefy martwe, krzywe i anti-deadzone, celowanie żyroskopem (prawa gałka / mysz);
  - profile per gra (`gra.exe`), które włączają się same, gdy gra jest aktywnym oknem;
  - zmiany działają na padzie od razu, zapis do `remap.json` jest osobno;
  - akcje nieodwracalne (wyłączenie pada, usunięcie profilu, reset, restart receivera) wymagają drugiego kliknięcia;
  - **Restart** w nagłówku okna i w menu zasobnika uruchamia receiver od nowa;
  - przyciski pada pokazane jako ikony (z pełną nazwą w podpowiedzi).
- **Skróty z PS:**

  | Skrót | Działanie |
  |---|---|
  | PS + Share | zrzut ekranu |
  | PS + d-pad góra / dół | czułość myszy |
  | PS + d-pad lewo / prawo | zmiana profilu |
  | długie PS | Xbox Game Bar albo Steam Big Picture |

- **Tryb konsoli:** podwójne kliknięcie wybranego przycisku pada (domyślnie **R2**) przełącza ekran i dźwięk między
  monitorem a telewizorem. R2 liczy się tylko przy głębokim naciśnięciu (z histerezą - drżenie spustu nie daje
  podwójnego kliknięcia) i tylko samo: R2 razem z innym przyciskiem (typowo w grze) nie przełącza. **Po połączeniu pada
  master tryb włącza się sam** (`auto_on_master`); podwójne R2 go wyłącza i zostaje wyłączony do następnego połączenia
  mastera. Restart receivera z okna zachowuje stan trybu. Stary `console.json` z PS / mute zmienia się sam na R2.
  **W grze tryb się nie przełącza:** podwójne R2 działa tylko, gdy na pierwszym planie jest program z listy
  `allowed_apps` (pulpit, Steam / Big Picture, launchery - lista do edycji w Ustawieniach); w grze pad mignie na
  czerwono, a włączenie po połączeniu mastera czeka, aż gra zejdzie z pierwszego planu.

  | | Ekran (`SetDisplayConfig`, awaryjnie `DisplaySwitch.exe`) | Dźwięk | Powiadomienie |
  |---|---|---|---|
  | ON | tylko TV (`/external`), na TV 2560×1440 i skalowanie 200 % (`tv_width`, `tv_height`, `tv_scale`) | `audio_tv` | „Tryb konsoli włączony” |
  | OFF | oba ekrany (`/extend`) | `audio_pc` | „Tryb konsoli wyłączony” |
  | brak pakietów z pada przez `timeout_s` | jak OFF | jak OFF | jak OFF |
  | wyłączenie / uśpienie PC (akcja mastera, Start, Big Picture) | jak OFF, przed wyłączeniem | jak OFF | brak |
  | start receivera (autostart) | `startup_mode`: `extend` (oba ekrany) albo `internal` | `audio_pc` | brak |

  Przy starcie domyślnie oba ekrany są aktywne, więc nie zostajesz bez obrazu. Zwykłe zamknięcie receivera przy
  włączonym trybie konsoli wraca do extend, tak samo wyłączenie Windows (Windows zapamiętuje układ ekranów - bez tego
  po starcie obraz, także ekran logowania, byłby tylko na TV). Błąd polecenia ekranu, dźwięku albo powiadomienia trafia do logu i nie
  przerywa działania.
- **Pad master** (np. gdy grają dzieci): wybrane funkcje działają tylko na jednym padzie - tryb konsoli, akcje
  systemowe, długie PS, PS w grze / Steam, zrzut ekranu, zmiana profilu, mysz, czułość. Inny pad dostaje czerwone
  mignięcie lightbara. Master jest przypisany do pada (MAC), nie do slotu. Szybka zmiana: korona na karcie pada albo
  **PS + R3 przytrzymane 3 s** na padzie (złoty błysk; można wyłączyć). Akcje systemowe mastera:

  | Domyślnie | Akcja |
  |---|---|
  | PS + Options | pauza gry (Esc) |
  | PS + △ (przytrzymaj ~1 s) | zamknij grę (nie zamyka pulpitu ani Steama) |
  | do ustawienia | uśpij komputer, wyłącz komputer (10 s na `shutdown /a`) |

  Przycisk z kombinacji (i samo PS) nie trafia do gry. Do akcji nie można użyć przycisków skrótów PS (Share, D-pad)
  ani R3. Gdy master nie jest podłączony: każdy może (domyślnie) albo nikt.
- **Ustawienia** (ikona w zasobniku → Ustawienia, albo przycisk w oknie), cztery zakładki:
  - **Tryb konsoli:** przycisk pada wybierasz naciśnięciem go na padzie, czasy to pola liczbowe, urządzenia audio to
    listy aktywnych wyjść Windows, „Przetestuj” włącza tryb na 10 s z wartościami z okna. Zmiana trybu konsoli zapisuje
    `console.json` i restartuje receiver (niezapisane zmiany profilu remapowania przepadają);
  - **Mysz:** czułość i stabilizacja kliknięcia touchpada;
  - **Master:** pad master, co wolno tylko jemu, przyciski akcji. Mysz i master działają od razu, bez restartu
    (`settings.json`);
  - **Okno:** ekran, na którym pojawia się okno (na środku; gdy ekranu nie ma, okno zostaje na dostępnym), okno albo
    pełny ekran (też F11 i przycisk w nagłówku), język: polski / English (zmiana po restarcie).
- **Edytor w przeglądarce** (opcjonalny, `remap_ui = true`): to samo co w oknie, w Edge/Chrome.
- **Wyłączanie pada** z PC albo samo po bezczynności (domyślnie 15 min).
- **Wibracja:** jednocześnie przez sterownik (evdev FF) i bezpośrednio raportem HID przez `/dev/hidrawN`
  (nadajnik przez `doas`), z flagami zależnymi od firmware pada; ustawienie `rumble` w `sender.toml`. Karta pada
  pokazuje „wibracja: sterownik + HID” albo powód jej braku (brak uprawnień / brak w jądrze / nie znaleziono pada /
  stary nadajnik). Przycisk wibracji na karcie pada sprawdza, czy wibracja dochodzi do pada.
- **Stała kolejność graczy:** nadajnik pamięta, który pad (MAC) był w którym slocie (`slots.json`), a receiver podłącza
  wirtualne DS4 w kolejności slotów, więc pad 1 zostaje graczem 1 w Steam / grze niezależnie od tego, który włączysz
  pierwszy.
- **Niskie opóźnienia:**
  - przyciski wysyłane natychmiast, z powtórkami na wypadek zgubionego pakietu,
  - gałki i ruch zlewane do 250 Hz i uśredniane,
  - priorytety wątków i procesów, timer 1 ms na Windows.

## Wymagania

| | Receiver (PC) | Sender (telefon / Linux) |
|---|---|---|
| System | Windows 10 / 11 | Linux z jądrem ze sterownikiem `hid-playstation` |
| Python | 3.11+ | 3.11+ |
| Inne | sterownik ViGEmBus, `vgamepad`, `PySide6-Essentials`, `pycaw`, `winotify` | `evdev`, `bluez` (`bluetoothctl`) |

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
| `rumble` | sender | auto | wibracja: `auto` (sterownik i raport HID naraz), `evdev`, `hidraw`, `off` |
| `key` | oba | "" | wspólny klucz HMAC (opcjonalne uwierzytelnianie pakietów) |
| `mouse_sens`, `scroll_sens` | receiver | 0.5, 0.6 | czułość myszy i przewijania touchpadem |
| `ps_modifier` | receiver | true | PS jako klawisz skrótów (PS idzie do gry po puszczeniu) |
| `ps_hold_action` | receiver | gamebar | długie PS: `gamebar`, `bigpicture`, `none` |
| `game_profiles` | receiver | true | automatyczny profil wg aktywnego okna |

Profile remapowania zapisują się w `receiver/remap.json`, przypisane do pada (MAC), a nie do slotu.

Tryb konsoli ma własny plik `receiver/console.json` (obok skryptu). Brak pliku albo błędna wartość = wartości domyślne
(plik jest tworzony i poprawiany, uszkodzony zostaje jako `console.json.bad`). Najwygodniej edytować w oknie
(Ustawienia), ręcznie też można:

| Klucz | Domyślnie | Co robi |
|---|---|---|
| `toggle_button` | `r2` | przycisk pada: `r2`, `l2`, `ps`, `share`, `options`, `cross`, `circle`, `triangle`, `square`, `l1`, `r1`, `l3`, `r3`, `dpad_*` |
| `double_click_ms` | 400 | maksymalny odstęp między dwoma naciśnięciami (100-2000) |
| `audio_tv` | "" | nazwa urządzenia audio TV (jak na liście w Ustawieniach; pusty = dźwięk bez zmian) |
| `audio_pc` | "" | nazwa urządzenia audio komputera |
| `timeout_s` | 60 | brak pakietów z pada przez tyle sekund = automatyczne OFF (5-3600) |
| `startup_mode` | `extend` | ekran przy starcie receivera: `extend` albo `internal` |
| `auto_on_master` | true | połączenie pada master włącza tryb konsoli |
| `allowed_apps` | pulpit, Steam, launchery | przełączanie trybu tylko przy tych programach na pierwszym planie |
| `tv_width`, `tv_height` | 2560, 1440 | rozdzielczość TV po włączeniu trybu (najwyższe odświeżanie dla niej; 0 = bez zmiany) |
| `tv_scale` | 200 | skalowanie TV w %: 100, 125, 150, 175, 200, 225, 250, 300... (0 = bez zmiany) |

Mysz touchpada i pad master są w `receiver/settings.json` (okno → Ustawienia → Mysz / Master, działa od razu).
Nadajnik zapisuje stały przydział pad → slot w `sender/slots.json` (`slots_file = ""` wyłącza).

## Rozwiązywanie problemów

| Objaw | Co sprawdzić |
|---|---|
| `WARNING: no reply from receiver` na telefonie | IP komputera, zapora Windows (UDP 5005), czy `key` jest taki sam po obu stronach |
| Wysoki ping / skoki w oknie receivera | Wi-Fi na telefonie; najlepiej Ethernet przez hub USB, wtedy wyłącz Wi-Fi (`nmcli radio wifi off`) |
| Gyro kręci w złą stronę | edytor → Gyro aim → „Invert horizontal / vertical” |
| Ruch odwrócony w emulatorze (DSU) | `acc_sign` / `gyro_sign` w konfiguracji receivera |
| Profil gry się nie włącza | gry z anti-cheatem mogą blokować odczyt nazwy procesu; przełącz ręcznie PS + d-pad |
| `virtual DS4 error ... ViGEmBus` | zainstaluj sterownik ViGEmBus |
| Pad nie wibruje | karta pada: „stary nadajnik” = skopiuj nowe pliki `sender/` na telefon, „brak uprawnień” = uruchom nadajnik przez `doas`, „brak w jądrze” = jądro telefonu bez `CONFIG_PLAYSTATION_FF` i `CONFIG_HIDRAW`, „nie znaleziono pada” = brak `/dev/hidraw` tego pada; na telefonie: `doas python3 sender_rumble.py --test` (wibruje każdym sposobem po kolei, bez sieci) i ustaw w `sender.toml` `rumble` na ten, który działa; log nadajnika `rumble via ...` i `rumble N/N from PC` (czy polecenie z PC dochodzi); przycisk „Wibracja” na karcie pada |
| Tryb konsoli nie reaguje | log receivera: `Console: double ... on controller N` = wykryte; `only the master pad can do that` = funkcja zastrzeżona dla mastera (Ustawienia → Master) |
| Gra w co-op widzi pady dziwnie (Steam) | Steam → biblioteka → gra → Właściwości → Kontroler: spróbuj „Wyłącz Steam Input” (gra czyta dwa DS4 sama) albo odwrotnie; kolejność graczy w Big Picture → Ustawienia → Kontroler |

## Struktura

```
receiver/   Windows: receiver_controller.py (start), rx_*.py (moduły), rx_qt*.py (okno),
            rx_web.py + rx_remap_ui.html (opcjonalny edytor w przeglądarce)
sender/     Linux:   sender_controller.py (start), sender_*.py (moduły)
tests/      testy na Linuksie z zaślepkami Windows i urządzeń evdev
```

`proto.py` (format pakietów) musi być identyczny w `receiver/` i `sender/`. Testy to sprawdzają.

## Testy

```
pip install evdev pyflakes PySide6-Essentials playwright   # playwright: test edytora w przeglądarce (opcjonalnie)
./tests/run_tests.sh
```

Testy obejmują:
- logikę obu stron: touchpad, gesty, limiter, przepełnienie bufora, skróty, profile, gyro, kształtowanie gałek;
- komunikację end-to-end przez UDP;
- start prawdziwych procesów oraz czyste zamknięcie przy restarcie z ustawień (porty UDP, wirtualne DS4, blokada instancji);
- tryb konsoli z zaślepionymi poleceniami ekranu, dźwięku i powiadomień (config, podwójne kliknięcie, auto-OFF, błędy);
- pad master (uprawnienia, akcje PS + przycisk, przejęcie), auto-ON trybu konsoli, R2 z histerezą,
  stabilizację kliku touchpada, `slots.json`;
- okno receivera (Qt offscreen, bez ekranu) i edytor w headless Chromium.

Działanie z prawdziwym padem, ViGEm, `DisplaySwitch.exe`, przełączaniem dźwięku (pycaw) i powiadomieniami Windows trzeba
sprawdzić ręcznie.
