# Konfiguracja

[English](CONFIGURATION.md) | **Polski**

Wszystkie ustawienia z wartościami domyślnymi i opisem są w `sender/sender.example.toml` i
`receiver/receiver.example.toml`. Skopiuj plik bez `.example` i odkomentuj potrzebne linie. Receiver sam wczytuje
`receiver.toml` leżący obok `receiver_controller.py`; nadajnik potrzebuje `--config sender.toml` (dodaje go instalator
usługi). Inny plik: `--config inny.toml`. Pliki `*.toml` są w `.gitignore`, bo mogą zawierać klucz.

Po zmianie wartości domyślnych nadajnik przy starcie sam poprawia w `sender.toml` linie ze **starymi wartościami
domyślnymi** (np. `pad_min_dt = 0.004` -> `0.001`); własne ustawienia, komentarze i układ pliku zostają, kopia w
`sender.toml.bak`. Każdy klucz jest migrowany tylko raz (lista w `sender.toml.migrated`), więc wartość ustawiona później
nigdy nie jest nadpisywana. Ręcznie: `python3 sender/sender_migrate.py sender/sender.toml`.

Najczęściej zmieniane:

| Ustawienie | Gdzie | Domyślnie | Co robi |
|---|---|---|---|
| `host` | sender | 192.168.1.4 (przykład - ustaw swój) | IP komputera z receiverem |
| `pad_min_dt` | sender | 0.001 | min. odstęp pakietów stanu (1 ms = 1000 Hz) |
| `idle_off_min` | sender | 15 | wyłącz pada po tylu minutach bez użycia, 0 = nigdy |
| `low_latency` | sender | true | procesor nadajnika bez głębokiego uśpienia + najszybsze rdzenie (wymaga roota) |
| `cpu_latency_us` | sender | 50 | `low_latency`: maks. czas wybudzenia procesora w µs |
| `rt_priority` | sender | 10 | SCHED_FIFO z tym priorytetem, `0` = zwykły planista (wymaga roota; działa też na musl - postmarketOS, Alpine) |
| `rumble` | sender | auto | wibracja: `auto` (sterownik i raport HID naraz), `evdev`, `hidraw`, `off` |
| `haptics_buffer_ms` | sender | 20 | haptyka HD: bufor jittera w ms (10-200); większy = pewniej przy słabym łączu, większe opóźnienie |
| `speaker_buffer_ms` | sender | 30 | głośnik pada (pad Bluetooth, wymaga libopus na senderze: `apt install libopus0` / `apk add opus`): bufor jittera w ms (20-200); większy = mniej przerw, większe opóźnienie |
| `speaker_volume` | sender | 100 | głośność głośnika pada (0-255) |
| `speaker_opus_bytes` | sender | 80 | bajtów Opus na ramkę 10 ms dla głośnika (30-200; 80 = 64 kbit/s); więcej = lepszy dźwięk, więcej ruchu Bluetooth |
| `speaker_opus_mode` | sender | hybrid | tryb Opus dla głośnika: `hybrid` (czysty dźwięk na padzie) albo `celt` (stary tryb: pad obniża poziom na brzegu każdej ramki, co brzmi robotycznie); hybrid kosztuje ok. 1 ms CPU więcej na ramkę |
| `haptics_motors` | sender | haptics | gdy gra wibruje podczas haptyki: `haptics` (silniki ignorowane, log raz) lub `rumble` (silniki wstrzymują haptykę) |
| `key` | oba | "" | wspólny klucz HMAC: podpisane pakiety i ochrona przed replay ([BEZPIECZENSTWO.pl.md](BEZPIECZENSTWO.pl.md)) |
| `dsu_bind` | receiver | 127.0.0.1 | adres serwera DSU; `0.0.0.0` = emulator na innym urządzeniu |
| `hm_min_age_days` | receiver | 7 | nieużywane, dopóki HIDMaestro jest przypięty do wersji (domyślnie); bez przypięcia nowe wydanie czeka tyle dni po publikacji; `0` = od razu |
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
| `double_click_ms` | 400 | maksymalny odstęp między dwoma naciśnięciami (200-2000) |
| `audio_tv` | "" | nazwa urządzenia audio TV (jak na liście w Ustawieniach; pusty = dźwięk bez zmian) |
| `audio_pc` | "" | nazwa urządzenia audio komputera |
| `timeout_s` | 60 | brak pakietów z pada przez tyle sekund = automatyczne OFF (5-3600) |
| `startup_mode` | `extend` | ekran przy starcie receivera: `extend` albo `internal` |
| `auto_on_master` | true | połączenie pada master włącza tryb konsoli |
| `allowed_apps` | pulpit, Steam, launchery | przełączanie trybu tylko przy tych programach na pierwszym planie; pusta lista = bez blokady |
| `tv_width`, `tv_height` | 2560, 1440 | rozdzielczość TV po włączeniu trybu (najwyższe odświeżanie dla niej; 0 = bez zmiany) |
| `tv_scale` | 200 | skalowanie TV w %: 100, 125, 150, 175, 200, 225, 250, 300... (0 = bez zmiany) |

Mysz touchpada, pad master, lightbar, haptyka gry, Wake-on-LAN, ustawienia dziennika i wirtualne pady są w `receiver/settings.json`
(okno → Ustawienia, działa od razu). Wirtualne pady (`vpad`): `backend` = `auto` | `hidmaestro` | `vigem`,
`fixed_slots` = stała kolejność graczy (pad zastępczy w pustym niższym slocie), `hm_auto` = pobieranie i aktualizacja
HIDMaestro z GitHuba (raz na dobę, do `receiver/hidmaestro/vX.Y.Z/`; nowa wersja działa po restarcie, gdy starsza jest
już w użyciu), `hm_dll` = własna `HIDMaestro.Core.dll` albo jej folder (wtedy bez pobierania). Preferencje okna (`ui`): `lang` = `pl` | `en`, `pads_mode` = `dynamic` (kolumna po lewej pokazuje
tylko podłączone pady) | `fixed`, `pads_count` = ile padów zawsze pokazuje `fixed` (1-4). Liczbę slotów ustawia `pads` w
`receiver.toml` (domyślnie 4, max 4). Nadajnik zapisuje ustawienia Wake-on-LAN z okna w `sender/wol.json`.
Nadajnik zapisuje stały przydział pad → slot w `sender/slots.json` (`slots_file = ""` wyłącza).
Kalibracja żyroskopu z okna (widok Test) jest w `sender/gyro_cal.json` (`gyro_cal_file = ""` - bez zapisu),
korekcja dryfu gałek i tryb dostępności w `receiver/padcal.json` (obok `remap.json`, w kopiach ustawień przy aktualizacji).

## Aktualizacja

Aktualizacja: przycisk **Aktualizuj** w oknie (albo w menu ikony w zasobniku) aktualizuje **obie strony**:
- receiver: `git pull` w folderze `dualsense-bridge`, przy zmianie `requirements.txt` także `pip install`;
- nadajnik: dostaje polecenie, robi `git pull` na nadajniku (jako właściciel folderu, także z usługi root)
  i sam uruchamia się od nowa;
- okno czeka na wynik nadajnika (do 20 s, z odliczaniem), pokazuje oba wyniki i restartuje receiver;
  nadajnik, który po tym czasie jeszcze pobiera, nie wstrzymuje restartu - jego wersję widać przy „Nadajnik online”.

W trakcie przycisk pokazuje kręcące się kółko i etapy w procentach (pobieranie, kopia ustawień, biblioteki, nadajnik).

Wersja (commit i data) jest w nagłówku okna obok nazwy, a wersja nadajnika przy „Nadajnik online”. Pod nagłówkiem
pojawia się pasek z informacją (przycisk **Aktualizuj** jest w menu bocznym), gdy:
- na GitHubie jest nowsza wersja - „Dostępna aktualizacja: 3 zmiany - tytuł najnowszej” (okno sprawdza to w tle
  20 s po starcie i co 30 min, bez pobierania kodu i bez okna logowania; raz też powiadomienie z zasobnika);
- nadajnik ma inną wersję niż receiver - „Nadajnik ma inną wersję (…) niż receiver (…)”. Wymaga klonów git po obu stronach (`git clone https://github.com/matt400/dualsense-bridge`, na nadajniku potem
`install-service.sh` z tego folderu) oraz Git for Windows.
Ustawienia i profile (`console.json`, `settings.json`, `remap.json`, `sender.toml`, `slots.json`) zostają nietknięte,
a po pobraniu nowej wersji receiver i tak robi ich kopię (Ustawienia → Kopie) - na wypadek, gdyby nowa wersja przy
starcie coś w nich zmieniła.

Klon z adresu https publicznego repozytorium aktualizuje się bez logowania. Klucz jest potrzebny tylko dla prywatnego
forka albo klonu przez SSH: usługa nadajnika działa jako root i nie ma Twojego klucza SSH, więc `git pull` kończy się
`Could not read from remote repository`. Wtedy jednorazowo na nadajniku, jako zwykły użytkownik (bez `sudo` / `doas`):

```
cd dualsense-bridge/sender
./setup-update-key.sh      # tworzy klucz tylko do odczytu w .git/ i wypisuje, co wkleić na GitHubie
./setup-update-key.sh      # po dodaniu klucza: sprawdza dostęp ("OK - nadajnik ma dostęp")
```

Powód nieudanej aktualizacji nadajnika jest widoczny w oknie na PC (np. `Permission denied (publickey)`).
Bez okna: `update.bat` w głównym folderze (tylko receiver).
