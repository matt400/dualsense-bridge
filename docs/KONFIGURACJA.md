# Konfiguracja

Wszystkie ustawienia z wartościami domyślnymi i opisem są w `sender/sender.example.toml` i
`receiver/receiver.example.toml`. Skopiuj plik bez `.example`, odkomentuj potrzebne linie i uruchom z
`--config sender.toml` / `--config receiver.toml`. Pliki `*.toml` są w `.gitignore`, bo mogą zawierać klucz.

Po zmianie wartości domyślnych nadajnik przy starcie sam poprawia w `sender.toml` linie ze **starymi wartościami
domyślnymi** (np. `pad_min_dt = 0.004` -> `0.001`); własne ustawienia, komentarze i układ pliku zostają, kopia w
`sender.toml.bak`. Ręcznie: `python3 sender/sender_migrate.py sender/sender.toml`.

Najczęściej zmieniane:

| Ustawienie | Gdzie | Domyślnie | Co robi |
|---|---|---|---|
| `host` | sender | 192.168.1.4 | IP komputera z receiverem |
| `pad_min_dt` | sender | 0.001 | min. odstęp pakietów stanu (1 ms = 1000 Hz) |
| `idle_off_min` | sender | 15 | wyłącz pada po tylu minutach bez użycia, 0 = nigdy |
| `low_latency` | sender | true | procesor telefonu bez głębokiego uśpienia + najszybsze rdzenie (wymaga `doas`) |
| `cpu_latency_us` | sender | 50 | `low_latency`: maks. czas wybudzenia procesora w µs |
| `rt_priority` | sender | 10 | SCHED_FIFO z tym priorytetem, `0` = zwykły planista (wymaga `doas`) |
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
| `allowed_apps` | pulpit, Steam, launchery | przełączanie trybu tylko przy tych programach na pierwszym planie; pusta lista = bez blokady |
| `tv_width`, `tv_height` | 2560, 1440 | rozdzielczość TV po włączeniu trybu (najwyższe odświeżanie dla niej; 0 = bez zmiany) |
| `tv_scale` | 200 | skalowanie TV w %: 100, 125, 150, 175, 200, 225, 250, 300... (0 = bez zmiany) |

Mysz touchpada, pad master, lightbar, Wake-on-LAN i ustawienia dziennika są w `receiver/settings.json` (okno →
Ustawienia, działa od razu). Nadajnik zapisuje ustawienia Wake-on-LAN z okna w `sender/wol.json`.
Nadajnik zapisuje stały przydział pad → slot w `sender/slots.json` (`slots_file = ""` wyłącza).
Kalibracja żyroskopu z okna (widok Test) jest w `sender/gyro_cal.json` (`gyro_cal_file = ""` - bez zapisu),
korekcja dryfu gałek w `receiver/padcal.json` (obok `remap.json`, w kopiach ustawień przy aktualizacji).

## Aktualizacja

Aktualizacja: przycisk **Aktualizuj** w oknie (albo w menu ikony w zasobniku) aktualizuje **obie strony**:
- receiver: `git pull` w folderze `dualsense-bridge`, przy zmianie `requirements.txt` także `pip install`;
- nadajnik: dostaje polecenie, robi `git pull` na telefonie (jako właściciel folderu, także z usługi root)
  i sam uruchamia się od nowa;
- okno czeka na wynik nadajnika (do 60 s), pokazuje oba wyniki i restartuje receiver.

Wersja (commit i data) jest w nagłówku okna obok nazwy, a wersja nadajnika przy „Nadajnik online”. Pod nagłówkiem
pojawia się pasek z przyciskiem **Aktualizuj**, gdy:
- na GitHubie jest nowsza wersja - „Dostępna aktualizacja: 3 zmiany - tytuł najnowszej” (okno sprawdza to w tle
  20 s po starcie i co 30 min, bez pobierania kodu i bez okna logowania; raz też powiadomienie z zasobnika);
- nadajnik ma inną wersję niż receiver - „Nadajnik ma inną wersję (…) niż receiver (…)”. Wymaga klonów git po obu stronach (`git clone https://github.com/matt400/dualsense-bridge`;
na telefonie potem `doas ./install-service.sh` z nowego folderu i `doas apk add git`) oraz Git for Windows.
Ustawienia i profile (`console.json`, `settings.json`, `remap.json`, `sender.toml`, `slots.json`) zostają nietknięte,
a po pobraniu nowej wersji receiver i tak robi ich kopię (Ustawienia → Kopie) - na wypadek, gdyby nowa wersja przy
starcie coś w nich zmieniła.

Repozytorium jest prywatne, a usługa nadajnika nie ma Twojego klucza SSH (brak agenta, klucz z hasłem) - git pull na
telefonie kończył się `Could not read from remote repository`. Jednorazowo na telefonie, jako zwykły użytkownik
(bez `doas`):

```
cd dualsense-bridge/sender
./setup-update-key.sh      # tworzy klucz tylko do odczytu w .git/ i wypisuje, co wkleić na GitHubie
./setup-update-key.sh      # po dodaniu klucza: sprawdza dostęp ("OK - nadajnik ma dostęp")
```

Powód nieudanej aktualizacji nadajnika jest widoczny w oknie na PC (np. `Permission denied (publickey)`).
Bez okna: `update.bat` w głównym folderze (tylko receiver).
