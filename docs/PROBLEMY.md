# Rozwiązywanie problemów

| Objaw | Co sprawdzić |
|---|---|
| `WARNING: no reply from receiver` w logu nadajnika | IP komputera, zapora Windows (UDP 5005), czy `key` jest taki sam po obu stronach |
| Wysoki ping / skoki w oknie receivera | Wi-Fi na nadajniku (najlepiej kabel Ethernet - przy telefonie przez hub USB; potem `nmcli radio wifi off`); nadajnik bez roota - log `Low latency: ... run with doas` (procesor nadajnika usypia się głęboko) |
| Gyro kręci w złą stronę | edytor → Gyro aim → „Invert horizontal / vertical” |
| Celownik / kamera powoli „płynie” sama | widok Test → wykres gyro przy leżącym padzie; jeśli nie jest płaski - „Kalibruj żyroskop” |
| Postać idzie sama / kamera się kręci (gałka) | widok Test → „Zmierz dryf” → „Zastosuj” (korekcja dryfu tego pada) |
| Ruch odwrócony w emulatorze (DSU) | `acc_sign` / `gyro_sign` w konfiguracji receivera |
| Profil gry się nie włącza | gry z anti-cheatem mogą blokować odczyt nazwy procesu; przełącz ręcznie PS + d-pad |
| `virtual pad error ... ViGEmBus` | zainstaluj sterownik ViGEmBus albo HIDMaestro (README) |
| Ustawienia → Pady: „HIDMaestro nieużywany” | „not found” = brak `receiver\hidmaestro\HIDMaestro.Core.dll` (albo zła ścieżka w Ustawieniach → Pady); „administrator rights required” = „Uruchom ponownie jako administrator”, a autostart podniesie uprawnienia sam przy takim starcie (dziennik: „Autostart ustawiony na uprawnienia administratora”); „pythonnet not installed” = `pip install pythonnet`; „.NET runtime” = zainstaluj .NET 10; inny błąd przy tworzeniu = ten pad przez ViGEmBus, szczegóły w dzienniku |
| Pad 2 jest graczem 1 | Ustawienia → Pady → „Stała kolejność graczy” włączona; który fizyczny pad jest padem 1, pamięta nadajnik (`sender/slots.json` - usuń wpis, żeby przydzielił slot od nowa) |
| Pad nie wibruje | karta pada: „stary nadajnik” = zaktualizuj nadajnik (Aktualizuj w oknie), „brak uprawnień” = nadajnik nie działa jako root (`sudo` / `doas` albo usługa z `install-service.sh`), „brak w jądrze” = jądro nadajnika bez `CONFIG_PLAYSTATION_FF` i `CONFIG_HIDRAW`, „nie znaleziono pada” = brak `/dev/hidraw` tego pada; na nadajniku: `sudo python3 sender_rumble.py --test` (wibruje każdym sposobem po kolei, bez sieci) i ustaw w `sender.toml` `rumble` na ten, który działa; log nadajnika `rumble via ...` i `rumble N/N from PC` (czy polecenie z PC dochodzi); przycisk „Wibracja” na karcie pada |
| Wake-on-LAN nie budzi komputera | czerwony błysk = nadajnik nie zna MAC (Ustawienia → Wake-on-LAN: wpisz go albo uruchom raz komputer z działającym nadajnikiem); biały błysk, a PC śpi = WoL wyłączony w BIOS / sterowniku karty, karta Wi-Fi albo „Szybkie uruchamianie” Windows (wyłącz je przy WoL z pełnego wyłączenia); log nadajnika `Wake-on-LAN ... -> ...` |
| Tryb konsoli włącza się dopiero po ~minucie od startu PC | dziennik: „Receiver uruchomiony N s po starcie Windows” - duże N = stary autostart z wpisu Run (Windows go opóźnia); receiver sam przenosi go do Harmonogramu zadań (wpis w dzienniku), ręcznie: `python receiver_controller.py --autostart on`. „Tryb konsoli czeka - na pierwszym planie X” = program X spoza listy w Ustawieniach → Tryb konsoli |
| Tryb konsoli nie reaguje | log receivera: `Console: double ... on controller N` = wykryte; `only the master pad can do that` = funkcja zastrzeżona dla mastera (Ustawienia → Master) |
| Panel Bluetooth: szara kropka / „brak danych” | nadajnik offline albo starszy niż v10 - Aktualizuj |
| Panel Bluetooth: czerwona kropka | opis w podpowiedzi: „brak uprawnień” = nadajnik nie działa jako root, „usługa bluetooth nie działa” = `sudo systemctl enable --now bluetooth`, „brak busctl” = system bez systemd, „zablokowany (rfkill)” = `sudo rfkill unblock bluetooth` |
| Sparuj: „parowanie odrzucone” / „brak odpowiedzi urządzenia” | pad nie jest w trybie parowania (Create + PS, aż lightbar szybko miga) - włącz go i uruchom skanowanie jeszcze raz; pad sparowany wcześniej z konsolą: Zapomnij, potem Sparuj |
| Gra w co-op widzi pady dziwnie (Steam) | Steam → biblioteka → gra → Właściwości → Kontroler: spróbuj „Wyłącz Steam Input” (gra czyta dwa DS4 sama) albo odwrotnie; kolejność graczy w Big Picture → Ustawienia → Kontroler |
