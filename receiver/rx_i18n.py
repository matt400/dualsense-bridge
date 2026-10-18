"""Receiver: język okna - polski (teksty w kodzie) albo angielski (słownik EN poniżej).

    _("Zapisz")                  -> "Zapisz" albo "Save"
    _("Pad {n}").format(n=2)     -> szablon tłumaczony, potem wstawiane wartości

Język wybiera się w oknie (Ustawienia -> Okno), zapis w settings.json; zmiana działa po restarcie receivera
(okno budowane jest raz). Logi w konsoli zostają po angielsku. tests/test_i18n.py sprawdza, że każdy tekst
z _("...") w kodzie ma tłumaczenie.
"""
LANGS = {"pl": "Polski", "en": "English"}
lang = "pl"


def set_lang(code):
    global lang
    lang = code if code in LANGS else "pl"


def _(text):
    return EN.get(text, text) if lang == "en" else text


EN = {
    "Odrzucono pakiety nadajnika: inny klucz HMAC (key) w nadajniku i w receiverze - ustaw ten sam albo usuń go z obu":
        "Sender packets rejected: the HMAC key differs between the sender and the receiver - set the same key on both "
        "or remove it from both",
    # ---------- okno główne ----------
    "Wynik": "Result",
    "Dostępna aktualizacja: {n}": "Update available: {n}",
    "Adres nadajnika: {ip}": "Sender address: {ip}",
    "Nadajnik ma inną wersję ({s}) niż receiver ({r}) - Aktualizuj pobierze tę samą na obu":
        "The sender has a different version ({s}) than the receiver ({r}) - Update gets the same one on both",
    "Kliknij Aktualizuj w oknie": "Click Update in the window",
    "{n} zmiana": "{n} change",
    "{n} zmiany": "{n} changes",
    "{n} zmian": "{n} changes",
    "Aktualizacja nie powiodła się: {err}": "Update failed: {err}",
    "Fizycznie": "Physical",
    "Pad {n}": "Pad {n}",
    "Wyłącz": "Power off",
    "Wyłączyć?": "Turn off?",
    "Rozłącza pada przez Bluetooth - pad się wyłącza": "Disconnects the pad over Bluetooth - it switches off",
    "Test wibracji": "Test rumble",
    "Krótka wibracja i mignięcie lightbara - sprawdza, czy wibracja dochodzi do pada":
        "A short rumble and lightbar flash - checks that rumble reaches the pad",
    "Pad master - kliknij, aby zdjąć": "Master pad - click to remove",
    "Kliknij: ten pad zostaje masterem": "Click: this pad becomes the master",
    "●  połączony": "●  connected",
    "●  brak sygnału": "●  no signal",
    "○  niepodłączony": "○  not connected",
    "b/d": "n/a",
    "Wirtualny pad: {pad}  ·  touchpad: {tp}": "Virtual pad: {pad}  ·  touchpad: {tp}",
    "dotyk w grze": "game touch",
    "wł.": "on",
    "wył.": "off",
    "mysz": "mouse",
    "profil: {name}": "profile: {name}",
    "domyślny": "default",
    "Touchpad jako mysz": "Touchpad as mouse",
    "Touchpad jako dotyk DS4": "Touchpad as DS4 touch",
    "Ustawienia": "Settings",
    "Tryb konsoli, mysz touchpada, pad master, okno, język": "Console mode, touchpad mouse, master pad, window, language",
    "Uruchamia receiver od nowa (pady na chwilę znikną, niezapisane zmiany profilu przepadną)":
        "Restarts the receiver (pads disappear for a moment, unsaved profile changes are lost)",
    "Zakończ": "Quit",
    "Zakończyć?": "Quit?",
    "Zatrzymuje receiver - wirtualne pady znikają": "Stops the receiver - the virtual pads disappear",
    "Pełny ekran (F11)": "Full screen (F11)",
    "Wyjdź z pełnego ekranu (F11)": "Exit full screen (F11)",
    "Kliknij część pada, aby ją zremapować.": "Click any part of the pad to remap it.",
    "Rysunek pokazuje:": "Drawing shows:",
    "Wynik = to, co dostaje gra po remapowaniu. Fizycznie = to, co robi sam pad.":
        "Result = what the game gets after your remaps. Physical = what the pad itself is doing.",
    "Wybrane": "Selected",
    "Gyro": "Gyro",
    "Zmiany": "Changes",
    "Zmiany ({n})": "Changes ({n})",
    "Zapisz": "Save",
    "Zapisz profil do remap.json (Ctrl+S)": "Write the profile to remap.json (Ctrl+S)",
    "Cofnij": "Revert",
    "Wróć do zapisanej wersji tego profilu": "Go back to the saved version of this profile",
    "Resetuj": "Reset all",
    "Zresetować?": "Reset?",
    "Usuń wszystkie zmiany w tym profilu": "Clear every change in this profile",
    "Pokaż okno": "Show window",
    "●  Nadajnik online": "●  Sender online",
    "●  Nadajnik offline": "●  Sender offline",
    " (ładuje się)": " (charging)",
    "Niska bateria": "Low battery",
    "DualSense bridge - NISKA BATERIA": "DualSense bridge - LOW BATTERY",
    "Nadajnik": "Sender",
    "Brak podłączonych padów": "No pads connected",
    # ---------- panele ----------
    "+ Gra": "+ Game",
    "Nowy profil dla gry": "New profile for a game",
    "Usuń": "Delete",
    "Usunąć?": "Sure?",
    "Nazwa programu jak w Menedżerze zadań → Szczegóły": "Program name as shown in Task Manager → Details",
    "Zacznij od bieżących ustawień": "Start from the current settings",
    "Utwórz": "Create",
    "Anuluj": "Cancel",
    "Program gry (jak w Menedżerze zadań → Szczegóły)": "Game program (as in Task Manager → Details)",
    "— wyłączony —": "— disabled —",
    "WYBRANE": "SELECTED",
    "Kliknij przycisk, spust albo gałkę na rysunku pada, aby ją zremapować lub dostroić.":
        "Click a button, trigger or stick on the controller to remap or tune it.",
    "Działa jak": "Acts as",
    "Domyślnie": "Default",
    "Strefa martwa (ignoruj lekkie dotknięcia do)": "Deadzone (ignore light touches up to)",
    "Pełne wciśnięcie od": "Full press at",
    "Domyślna reakcja": "Default response",
    "Gra widzi dane tej gałki z": "The game sees this stick's data from",
    "Odwróć X": "Invert X",
    "Odwróć Y": "Invert Y",
    "REAKCJA": "RESPONSE",
    "Strefa martwa": "Deadzone",
    "Pełne wychylenie od": "Full tilt at",
    "Krzywa (powyżej 1 = dokładniejsze małe ruchy)": "Curve (above 1 = finer small moves)",
    "Anty-strefa martwa (pokonuje strefę martwą gry)": "Anti-deadzone (beats the game's own deadzone)",
    "Klik gałki ({b}) działa jak": "Stick click ({b}) acts as",
    "REAKCJA {b} (TO, CO DOSTAJE GRA)": "{b} RESPONSE (WHAT THE GAME GETS)",
    "CELOWANIE ŻYROSKOPEM": "GYRO AIM",
    "Tryb": "Mode",
    "Obracaj padem, aby celować: jako prawa gałka (każda gra) albo jako mysz.":
        "Turn the pad to aim: as the right stick (any game) or as a mouse.",
    "Zawsze": "Always",
    "Aktywne (zawsze albo przy trzymanym przycisku)": "Active (always, or while holding the button)",
    "Czułość": "Sensitivity",
    "Odwróć w poziomie": "Invert horizontal",
    "Odwróć w pionie": "Invert vertical",
    "Anty-strefa martwa (małe obroty też ruszają widokiem)": "Anti-deadzone (small turns still move the view)",
    "Ten profil nie jest teraz używany.": "This profile is not in use right now.",
    "● Gyro aktywne": "● Gyro active",
    "Gyro nieaktywne": "Gyro idle",
    "Gyro nieaktywne — przytrzymaj {b}": "Gyro idle — hold {b}",
    "Usuń tę zmianę": "Remove this change",
    "Brak zmian - wszystko działa domyślnie.": "No changes - everything works as default.",
    # ---------- przyciski i gałki ----------
    "Krzyżyk (X)": "Cross (X)",
    "Kółko (O)": "Circle (O)",
    "Trójkąt": "Triangle",
    "Kwadrat": "Square",
    "L1": "L1",
    "R1": "R1",
    "Share / Create": "Share / Create",
    "Options": "Options",
    "Przycisk PS": "PS button",
    "L3 (gałka)": "L3 (stick)",
    "R3 (gałka)": "R3 (stick)",
    "D-pad góra": "D-pad Up",
    "D-pad dół": "D-pad Down",
    "D-pad lewo": "D-pad Left",
    "D-pad prawo": "D-pad Right",
    "L2 (spust)": "L2 (trigger)",
    "R2 (spust)": "R2 (trigger)",
    "Lewa gałka": "Left stick",
    "Prawa gałka": "Right stick",
    "Wyłączona (środek)": "Disabled (centered)",
    "Lewa gałka (klik = L3)": "Left stick (click = L3)",
    "Prawa gałka (klik = R3)": "Right stick (click = R3)",
    "Wyłączone": "Off",
    "Prawa gałka (działa w każdej grze)": "Right stick (works in every game)",
    "Mysz (strzelanki na PC, menu)": "Mouse (PC shooters, menus)",
    "TOUCHPAD": "TOUCHPAD",
    # ---------- edytor profilu ----------
    "Wpisz nazwę pliku .exe gry, np. eldenring.exe": "Type the game's .exe file name, e.g. eldenring.exe",
    "Domyślny": "Default",
    "● = profil teraz w użyciu": "● = profile in use right now",
    "● Zastosowano na żywo - jeszcze niezapisane": "● Applied live - not saved yet",
    "● Profil gry jeszcze niezapisany": "● Game profile not saved yet",
    "✓ Zgodny z zapisanym profilem": "✓ Matches saved profile",
    "Używany teraz.": "In use right now.",
    "Używany automatycznie, gdy aktywnym oknem jest {name}.": "Used automatically while {name} is the active window.",
    "Używany, gdy żaden profil gry nie pasuje do aktywnego okna.": "Used when no game profile matches the active window.",
    "{text}  Aktywne okno: {game}.": "{text}  Active window: {game}.",
    "wyłączony": "disabled",
    "odwróć X": "invert X",
    "odwróć Y": "invert Y",
    "środek": "centered",
    "zawsze": "always",
    "przytrzymaj {b}": "hold {b}",
    "prawa gałka": "right stick",
    "Gyro: {mode}, {hold}, czułość {sens}×": "Gyro: {mode}, {hold}, sensitivity {sens}×",
    "Reakcja {b}: strefa martwa {dz}, pełne od {mx}": "{b} response: deadzone {dz}, full at {mx}",
    "strefa martwa": "deadzone",
    "pełne od": "full at",
    "krzywa": "curve",
    "anty-strefa martwa": "anti-deadzone",
    "{stick} - reakcja: {parts}": "{stick} response: {parts}",
    "Utworzono profil dla {exe} - zapisz go, żeby go zachować": "Profile for {exe} created - save it to keep it",
    "Profil usunięty": "Profile deleted",
    "Zastosowano (niezapisane)": "Applied (not saved)",
    "usuwać można tylko profile gier": "only game profiles can be deleted",
    "Zapisano - {file}": "Saved - {file}",
    "Usunięto - {file}": "Deleted - {file}",
    "Usunięto": "Deleted",
    "Zapis nieudany: {err}": "Save failed: {err}",
    # ---------- łącze ----------
    "ping {ms} ms": "ping {ms} ms",
    "strata {pct}%": "loss {pct}%",
    "pad {hz} Hz": "pad {hz} Hz",
    "{n} pkt/s": "{n} pkt/s",
    # ---------- okno ustawień ----------
    "Tryb konsoli": "Console mode",
    "Mysz": "Mouse",
    "Master": "Master",
    "Okno": "Window",
    "Przetestuj": "Test",
    "Włącza tryb konsoli na kilka sekund z wartościami z tego okna, potem wraca do trybu rozszerzonego":
        "Turns console mode on for a few seconds with the values from this window, then back to extended mode",
    "Podwójne kliknięcie wybranego przycisku pada przełącza ekran i dźwięk między monitorem a telewizorem. "
    "Brak sygnału z pada przez ustawiony czas wraca do trybu rozszerzonego (oba ekrany).":
        "Double-pressing the chosen pad button switches the screen and sound between the monitor and the TV. "
        "No signal from the pad for the set time returns to extended mode (both screens).",
    "Przycisk pada (podwójne kliknięcie)": "Pad button (double press)",
    "Wybierz na padzie": "Pick on the pad",
    "Naciśnij przycisk na padzie… (klik = anuluj)": "Press a button on the pad… (click = cancel)",
    "Odstęp między kliknięciami": "Time between presses",
    "Auto-OFF po braku sygnału z pada": "Auto-OFF after no signal from the pad",
    "Dźwięk po włączeniu (TV)": "Sound when on (TV)",
    "Dźwięk po wyłączeniu (komputer)": "Sound when off (computer)",
    "Odśwież listę urządzeń": "Refresh device list",
    "— nie zmieniaj dźwięku —": "— don't change sound —",
    "{name} (niedostępne)": "{name} (unavailable)",
    "Rozdzielczość TV po włączeniu": "TV resolution when on",
    "Skalowanie TV": "TV scaling",
    "— bez zmiany —": "— no change —",
    "Ekran przy starcie receivera": "Screen when the receiver starts",
    "Rozszerz: oba ekrany aktywne": "Extend: both screens active",
    "Tylko monitor": "Monitor only",
    "Włącz tryb konsoli po połączeniu pada master (wyłączasz go tym samym podwójnym kliknięciem)":
        "Turn console mode on when the master pad connects (turn it off with the same double press)",
    "Przełączanie działa tylko, gdy na pierwszym planie jest jeden z tych programów (jeden w linii) - "
    "w grze podwójne kliknięcie nic nie robi, a włączenie po połączeniu mastera czeka na wyjście z gry. "
    "Pusta lista = bez blokady.":
        "Switching works only while one of these programs is in the foreground (one per line) - in a game the "
        "double press does nothing and turning on after the master connects waits until you leave the game. "
        "Empty list = no blocking.",
    "Dozwolone programy (pulpit, Steam, launchery)": "Allowed programs (desktop, Steam, launchers)",
    "Przywróć domyślną listę": "Restore default list",
    "Zmiana trybu konsoli lub języka uruchamia receiver od nowa (pady na chwilę znikną). Niezapisane zmiany "
    "profilu (Zapisz w oknie głównym) zostaną utracone.":
        "Changing console mode or language restarts the receiver (pads disappear for a moment). Unsaved profile "
        "changes (Save in the main window) are lost.",
    "Touchpad jako mysz (PS + klik touchpada przełącza). Zmiany działają od razu po zapisie.":
        "Touchpad as a mouse (PS + touchpad click toggles). Changes apply right after saving.",
    "Czułość kursora": "Cursor sensitivity",
    "Kursor stoi po wciśnięciu i puszczeniu kliku [ms]": "Cursor holds still after press and release [ms]",
    "Cofnij ruch sprzed kliknięcia [ms] (docisk palca przesuwa kursor)":
        "Undo movement before the click [ms] (pressing the finger moves the cursor)",
    "Przeciąganie z wciśniętym klikiem zaczyna się po (jednostki touchpada)":
        "Dragging with the click held starts after (touchpad units)",
    "Domyślne": "Defaults",
    "0 ms wyłącza daną poprawkę. Gdy kursor wciąż ucieka przy kliknięciu - zwiększ oba czasy; gdy przeciąganie "
    "(np. okna) rusza za późno - zmniejsz ostatni suwak.":
        "0 ms turns a fix off. If the cursor still moves when clicking - raise both times; if dragging "
        "(e.g. a window) starts too late - lower the last slider.",
    "Master to jeden pad z dodatkowymi uprawnieniami (np. gdy grają dzieci). Szybka zmiana: korona na karcie "
    "pada w oknie głównym albo przytrzymanie PS + R3 na padzie.":
        "The master is one pad with extra rights (e.g. when kids play). Quick change: the crown on the pad card "
        "in the main window or holding PS + R3 on the pad.",
    "Pad master": "Master pad",
    "PS + R3 przytrzymane {s} s na padzie = ten pad zostaje masterem": "PS + R3 held for {s} s on a pad = it becomes the master",
    "TYLKO MASTER MOŻE": "ONLY THE MASTER CAN",
    "Gdy master nie jest podłączony": "When the master is not connected",
    "AKCJE: PS + PRZYCISK": "ACTIONS: PS + BUTTON",
    "  (przytrzymaj)": "  (hold)",
    "Przycisk z kombinacji nie trafia do gry. Akcje z dopiskiem \"przytrzymaj\" wymagają trzymania ok. 1 s "
    "(krótka wibracja = trzymaj dalej). Wyłączenie PC daje 10 s na \"shutdown /a\".":
        "The combo button does not reach the game. Actions marked \"hold\" need about 1 s (a short rumble = keep "
        "holding). Shutting down the PC gives 10 s for \"shutdown /a\".",
    "— brak mastera (każdy pad może wszystko) —": "— no master (every pad can do everything) —",
    "Pad {n}  ·  {key}": "Pad {n}  ·  {key}",
    "{key}  (niepodłączony)": "{key}  (not connected)",
    "Ekran, na którym pojawia się okno DualSense bridge": "Screen the DualSense bridge window opens on",
    "Okno otwiera się (z ikony w zasobniku, przy starcie) na środku wybranego ekranu. Gdy tego ekranu nie ma "
    "(np. monitor wyłączony w trybie konsoli), okno zostaje na dostępnym ekranie.":
        "The window opens (from the tray icon, at start) in the middle of the chosen screen. If that screen is "
        "missing (e.g. the monitor is off in console mode), the window stays on an available screen.",
    "Tam, gdzie okno było ostatnio": "Where the window was last",
    "Ekran {n}: {name}  ({w}×{h}){prim}": "Screen {n}: {name}  ({w}×{h}){prim}",
    "  · główny": "  · primary",
    "{key} (teraz niepodłączony)": "{key} (not connected now)",
    "Wygląd okna": "Window mode",
    "Okno (F11 przełącza)": "Window (F11 toggles)",
    "Pełny ekran": "Full screen",
    "Język": "Language",
    "Ten sam przycisk przypisano do dwóch akcji mastera.": "The same button is assigned to two master actions.",
    "Nie udało się zapisać {path}: {err}": "Could not save {path}: {err}",
    "✓ Zapisano - działa od razu.": "✓ Saved - works right away.",
    "Nie można odczytać urządzeń audio: {err} (pip install pycaw)": "Cannot read audio devices: {err} (pip install pycaw)",
    "Tryb konsoli nie działa w tym uruchomieniu.": "Console mode is not running in this session.",
    "Test: TV i dźwięk TV na {s} s, potem powrót do trybu rozszerzonego.":
        "Test: TV and TV sound for {s} s, then back to extended mode.",
    # ---------- pad master / mysz (rx_settings) ----------
    "Tryb konsoli (ekran i dźwięk)": "Console mode (screen and sound)",
    "Akcje systemowe (PS + przycisk)": "System actions (PS + button)",
    "Przytrzymanie PS: Game Bar / Big Picture": "Holding PS: Game Bar / Big Picture",
    "PS w grze i w Steam (menu, nakładka)": "PS in games and Steam (menu, overlay)",
    "Zrzut ekranu (PS + Share)": "Screenshot (PS + Share)",
    "Zmiana profilu (PS + ← / →)": "Profile change (PS + ← / →)",
    "Touchpad jako mysz (PS + klik)": "Touchpad as mouse (PS + click)",
    "Czułość myszy (PS + ↑ / ↓)": "Mouse sensitivity (PS + ↑ / ↓)",
    "Pauza gry (Esc)": "Pause game (Esc)",
    "Zamknij grę": "Close game",
    "Uśpij komputer": "Sleep the PC",
    "Wyłącz komputer": "Shut down the PC",
    "każdy pad może wszystko": "every pad can do everything",
    "zastrzeżone funkcje są wyłączone": "restricted features are off",
    "Pad {n} nie przysłał jeszcze identyfikatora (MAC) - spróbuj za chwilę":
        "Pad {n} has not sent its ID (MAC) yet - try again in a moment",
    "Bez zmian": "No change",
    "Zapisano": "Saved",
    # ---------- tryb konsoli (powiadomienia) ----------
    "Tryb konsoli włączony": "Console mode on",
    "Tryb konsoli wyłączony": "Console mode off",
    "Brak sygnału z pada przez {s} s": "No signal from the pad for {s} s",
    "koniec testu": "end of test",
    "połączono pad master (pad {n})": "master pad connected (pad {n})",
    "po restarcie receivera": "after receiver restart",
    "zamknięcie receivera": "receiver closing",
    "wyłączanie komputera": "shutting down the PC",
    "usypianie komputera": "putting the PC to sleep",
    "wyłączanie Windows": "Windows shutting down",
    # ---------- układ okna (sekcje, przyciski) ----------
    "PRZEŁĄCZANIE": "SWITCHING",
    "TELEWIZOR I DŹWIĘK": "TV AND SOUND",
    "BLOKADA W GRZE": "IN-GAME LOCK",
    "KURSOR": "CURSOR",
    "KLIKNIĘCIE": "CLICK",
    "OKNO": "WINDOW",
    "JĘZYK": "LANGUAGE",
    "PAD MASTER": "MASTER PAD",
    "Włącz po połączeniu pada master (bez mastera: pierwszego pada)":
        "Turn on when the master pad connects (no master: the first pad)",
    "Wyłączasz go tym samym podwójnym kliknięciem; zostaje wyłączony do następnego połączenia mastera.":
        "Turn it off with the same double press; it stays off until the master connects again.",
    "Zmiana języka działa po restarcie receivera.": "The language changes after the receiver restarts.",
    "Przejęcie na padzie: PS + R3 przez {s} s": "Take over on the pad: PS + R3 for {s} s",
    "Wibracja": "Rumble",
    # ---------- wibracja ----------
    "wibracja: stary nadajnik": "rumble: old sender",
    "Skopiuj nowe pliki sender/ na telefon - starszy nadajnik nie wibruje bez obsługi wibracji w jądrze.":
        "Copy the new sender/ files to the phone - an older sender cannot rumble without kernel rumble support.",
    "wibracja: niedostępna": "rumble: unavailable",
    "Na telefonie: doas python3 sender_rumble.py --test": "On the phone: doas python3 sender_rumble.py --test",
    "wibracja: wyłączona": "rumble: off",
    "W sender.toml ustaw rumble = \"auto\".": "Set rumble = \"auto\" in sender.toml.",
    "wibracja: brak uprawnień": "rumble: no permission",
    "Nadajnik nie może otworzyć /dev/hidraw - uruchom go przez doas albo zainstaluj usługę: doas ./install-service.sh":
        "The sender cannot open /dev/hidraw - run it with doas or install the service: doas ./install-service.sh",
    "wibracja: brak w jądrze": "rumble: missing in kernel",
    "Jądro telefonu nie ma CONFIG_PLAYSTATION_FF ani CONFIG_HIDRAW - bez jednej z nich pad nie wibruje.":
        "The phone kernel has neither CONFIG_PLAYSTATION_FF nor CONFIG_HIDRAW - the pad cannot rumble without one of them.",
    "wibracja: nie znaleziono pada": "rumble: pad not found",
    "Nadajnik nie znalazł /dev/hidraw tego pada - na telefonie: doas python3 sender_rumble.py --test":
        "The sender did not find /dev/hidraw of this pad - on the phone: doas python3 sender_rumble.py --test",
    "sterownik": "driver",
    "wibracja: {ways}": "rumble: {ways}",
    # ---------- aktualizacja ----------
    "Aktualizuj": "Update",
    "Aktualizować?": "Update?",
    "Aktualizuję…": "Updating…",
    "Pobieranie aktualizacji…": "Downloading update…",
    "Pobiera nową wersję z GitHuba (git pull) dla receivera i nadajnika; oba uruchamiają się od nowa":
        "Downloads the new version from GitHub (git pull) for the receiver and the sender; both restart",
    "wersja {v}": "version {v}",
    "nieznana": "unknown",
    "Ostatnia zmiana: {s}": "Latest change: {s}",
    "Wersja nieznana - folder nie jest klonem git": "Version unknown - the folder is not a git clone",
    "Nadajnik: wersja {s}, receiver: {r} - kliknij Aktualizuj": "Sender: version {s}, receiver: {r} - click Update",
    "Nadajnik: wersja {s}": "Sender: version {s}",
    "Nadajnik nie podaje wersji (starsza wersja albo nie klon git)":
        "The sender does not report its version (older version or not a git clone)",
    "nadajnik niepołączony - zaktualizuje się następnym razem": "sender not connected - it will be updated next time",
    "nadajnik w starej wersji - zaktualizuj go raz ręcznie (git pull na telefonie)":
        "the sender is too old - update it once by hand (git pull on the phone)",
    "nadajnik: najnowsza wersja": "sender: latest version",
    "nadajnik: zaktualizowany, uruchamia się od nowa": "sender: updated, restarting",
    "nadajnik: folder na telefonie to nie klon git - na telefonie: "
    "git clone https://github.com/matt400/dualsense-bridge i doas ./install-service.sh z nowego folderu":
        "sender: the folder on the phone is not a git clone - on the phone: git clone https://github.com/matt400/dualsense-bridge "
        "and doas ./install-service.sh from the new folder",
    "nadajnik: brak git na telefonie (doas apk add git)": "sender: git missing on the phone (doas apk add git)",
    "nadajnik: git pull nie powiódł się (szczegóły w logu na telefonie)": "sender: git pull failed (details in the phone log)",
    "nadajnik: git pull nie powiódł się: {msg}": "sender: git pull failed: {msg}",
    "usługa na telefonie nie ma dostępu do prywatnego repozytorium - na telefonie raz: "
    "sender/setup-update-key.sh (klucz tylko do odczytu, instrukcja w skrypcie)":
        "the phone service has no access to the private repository - run once on the phone: "
        "sender/setup-update-key.sh (read-only key, the script explains the steps)",
    "nadajnik nie odpowiedział": "the sender did not respond",
    "Folder nie jest repozytorium git (pobrany jako ZIP?) - sklonuj: git clone https://github.com/matt400/dualsense-bridge":
        "The folder is not a git repository (downloaded as ZIP?) - clone it: git clone https://github.com/matt400/dualsense-bridge",
    "Nie znaleziono git - zainstaluj Git for Windows (git-scm.com)": "git not found - install Git for Windows (git-scm.com)",
    "git pull nie powiódł się: {msg}": "git pull failed: {msg}",
    "Masz najnowszą wersję": "You have the latest version",
    "git nie odpowiada: {msg}": "git does not respond: {msg}",
    "Zaktualizowano ({n} zmian): {old} → {new}": "Updated ({n} changes): {old} → {new}",
    "pip nie zadziałał ({msg}) - uruchom: pip install -r receiver\\requirements.txt":
        "pip failed ({msg}) - run: pip install -r receiver\\requirements.txt",
    "pip: {msg}": "pip: {msg}",
    "nowe biblioteki zainstalowane": "new libraries installed",
    "Uruchom ponownie receiver (przycisk Restart w oknie), żeby działał nowy kod.":
        "Restart the receiver (Restart button in the window) to run the new code.",
    # ---------- ikony ----------
    "Ekran": "Screen",
    "Uruchom ponownie": "Restart",
    "Kopie ustawień": "Settings backups",
    "Opóźnienie": "Latency",
    "Nowy profil gry": "New game profile",
    # ---------- opóźnienie pad -> gra ----------
    "Opóźnienie pad → gra": "Latency pad → game",
    "średnia · {stats} ms · {n} próbek z 5 s": "average · {stats} ms · {n} samples from 5 s",
    "Bluetooth (ponad minimum)": "Bluetooth (above minimum)",
    "Telefon (jądro → wysłanie)": "Phone (kernel → send)",
    "Sieć": "Network",
    "PC (odbiór → wirtualny pad)": "PC (receive → virtual pad)",
    "Brak pomiaru: pad leży albo nadajnik jest w starszej wersji (Aktualizuj).":
        "No measurement: the pad is idle or the sender is an older version (Update).",
    "Średnie odcinków. Stała część radia Bluetooth (~1–2 ms) jest niemierzalna.":
        "Segment averages. The fixed part of the Bluetooth radio (~1–2 ms) cannot be measured.",
    "zwykle ≤ {p95} · max {max}": "usually ≤ {p95} · max {max}",
    "zwykle = 95 na 100 pomiarów było tak szybkich albo szybszych, max = najwolniejszy pomiar":
        "usually = 95 of 100 measurements were this fast or faster, max = the slowest measurement",
    "Przycisk → gra": "Button → game",
    "średnia · {stats} ms · {n} z ostatnich {keep} naciśnięć": "average · {stats} ms · {n} of the last {keep} presses",
    "Tylko przyciski, D-pad i spusty (wciśnięcie / puszczenie) - bez gałek i żyroskopu.":
        "Buttons, D-pad and triggers only (press / release) - no sticks or gyro.",
    "Wyzeruj pomiar przycisk → gra": "Reset the button → game measurement",
    "Wyzeruj": "Reset",
    # ---------- efekty spustów ----------
    "EFEKT SPUSTU": "TRIGGER EFFECT",
    "Efekt": "Effect",
    "Jak w profilu pada": "As in the pad profile",
    "W tej grze spust działa tak, jak ustawiono w profilu domyślnym pada.":
        "In this game the trigger works as set in the pad's default profile.",
    "Wyłączony": "Off",
    "Spust bez efektu.": "Trigger without an effect.",
    "Opór": "Resistance",
    "Stały opór od wybranego miejsca skoku, np. gaz i hamulec w wyścigach.":
        "Constant resistance from the chosen point of travel, e.g. throttle and brake in racing games.",
    "Klik": "Click",
    "Opór narasta do wybranego punktu i puszcza z kliknięciem - jak spust broni.":
        "Resistance builds up to the chosen point and releases with a click - like a gun trigger.",
    "Drgania": "Vibration",
    "Spust drga stale od wybranego miejsca skoku.": "The trigger vibrates constantly from the chosen point of travel.",
    "Od (skok spustu)": "From (trigger travel)",
    "Punkt kliku (skok spustu)": "Click point (trigger travel)",
    "Siła": "Strength",
    "Siła drgań": "Vibration strength",
    "Częstotliwość drgań": "Vibration frequency",
    "Gra przez DS4 nie ustawi efektów DualSense, więc robi to nadajnik. W profilu gry „Jak w profilu pada” bierze "
    "ustawienie z profilu domyślnego.":
        "A game talking to a DS4 can't set DualSense effects, so the sender does. In a game profile "
        "“As in the pad profile” takes the setting from the default profile.",
    "WIBRACJA Z GRY NA SPUSTACH": "GAME RUMBLE ON TRIGGERS",
    "Wibracja z gry": "Game rumble",
    "Włączona": "On",
    "Wyłączona": "Off",
    "Siła (względem wibracji pada)": "Strength (relative to pad rumble)",
    "Namiastka wibracji spustów: L2 drga jak duży silnik pada, R2 jak mały. Na czas wibracji zastępuje efekt spustu, "
    "po chwili ciszy efekt wraca. Wspólne dla L2 i R2.":
        "A stand-in for trigger rumble: L2 vibrates like the pad's large motor, R2 like the small one. While it "
        "vibrates it replaces the trigger effect, which comes back after a short pause. Shared by L2 and R2.",
    "Klik i wibracja spustu wymagają nowszego nadajnika - kliknij Aktualizuj (opór działa).":
        "Trigger click and vibration need a newer sender - click Update (resistance works).",
    "Efekty spustów są tylko dla pada master (Ustawienia -> Master).":
        "Trigger effects are for the master pad only (Settings -> Master).",
    "Efekty spustów (opór, klik, wibracja)": "Trigger effects (resistance, click, vibration)",
    "Klik {b}: w {pos}%, siła {force}/8": "Click {b}: at {pos}%, strength {force}/8",
    "Wibracja {b}: od {start}%, siła {amp}/8, {freq} Hz": "Vibration {b}: from {start}%, strength {amp}/8, {freq} Hz",
    "Efekt {b}: wyłączony": "Effect {b}: off",
    "Wibracja z gry na spustach: siła {gain}%, {freq} Hz": "Game rumble on triggers: strength {gain}%, {freq} Hz",
    "Wibracja z gry na spustach: wyłączona": "Game rumble on triggers: off",
    "klik {n}": "click {n}",
    "drgania {n}": "vibr {n}",
    "bez efektu": "no effect",
    "wibr. gry": "game rumble",
    "Nadajnik w starszej wersji nie ustawia oporu - kliknij Aktualizuj.":
        "An older sender can't set the resistance - click Update.",
    "Opór wymaga raportu HID na telefonie (jak wibracja przez HID) - nadajnik go nie ma.":
        "Resistance needs the HID report on the phone (like rumble via HID) - the sender doesn't have it.",
    "Opór {b}: od {start}%, siła {force}/8": "Resistance {b}: from {start}%, strength {force}/8",
    "opór {n}": "resist {n}",
    "ZAAWANSOWANE: REAKCJA SPUSTU": "ADVANCED: TRIGGER RESPONSE",
    "ZAAWANSOWANE: REAKCJA GAŁKI": "ADVANCED: STICK RESPONSE",
    "Usuń wszystkie": "Remove all",
    "Usunąć wszystkie?": "Remove all?",
    # ---------- kopie ustawień ----------
    "Kopie": "Backups",
    "KOPIE USTAWIEŃ": "SETTINGS BACKUPS",
    "Przed każdą aktualizacją receiver kopiuje settings.json, console.json, remap.json i padcal.json (zostaje {n} ostatnich). "
    "Przywrócenie najpierw zapisuje kopię bieżących plików - też da się je cofnąć - a potem uruchamia receiver od nowa.":
        "Before every update the receiver copies settings.json, console.json, remap.json and padcal.json (the last {n} are kept). "
        "Restoring first backs up the current files - so it can be undone too - and then restarts the receiver.",
    "Zrób kopię teraz": "Back up now",
    "Nie ma jeszcze żadnej kopii.": "No backups yet.",
    "Przywróć": "Restore",
    "Przywrócić?": "Restore?",
    "Zastępuje bieżące ustawienia tymi z kopii i uruchamia receiver od nowa":
        "Replaces the current settings with the ones from the backup and restarts the receiver",
    "Nie udało się zrobić kopii: {err}": "Could not create a backup: {err}",
    "✓ Kopia zapisana.": "✓ Backup saved.",
    "Nie ma jeszcze plików ustawień do skopiowania.": "There are no settings files to back up yet.",
    "przed aktualizacją": "before update",
    "przed przywróceniem kopii": "before restoring a backup",
    "zrobiona ręcznie": "made manually",
    "Nie ma takiej kopii": "No such backup",
    "Kopia jest pusta": "The backup is empty",
    "Nie udało się przywrócić kopii: {err}": "Could not restore the backup: {err}",
    "Przywrócono kopię z {when} - receiver uruchomi się od nowa": "Restored the backup from {when} - the receiver will restart",
    "kopia ustawień zapisana": "settings backed up",
    "brak ustawień do skopiowania": "no settings to back up",
    "kopia ustawień nieudana: {err}": "settings backup failed: {err}",
    # ---------- dziennik zdarzeń ----------
    "Dziennik zdarzeń": "Event log",
    "DZIENNIK ZDARZEŃ": "EVENT LOG",
    "Schowaj dziennik zdarzeń": "Hide the event log",
    "Połączenia padów, master, tryb konsoli, błędy, aktualizacje": "Pad connections, master, console mode, errors, updates",
    "Wszystko": "Everything",
    "Ostrzeżenia i błędy": "Warnings and errors",
    "Tylko błędy": "Errors only",
    "Które wpisy pokazywać": "Which entries to show",
    " linii": " lines",
    "Limit linii w panelu (starsze znikają)": "Line limit in the panel (older lines disappear)",
    "Kopiuj": "Copy",
    "Kopiuj widoczne wpisy do schowka": "Copy the visible entries to the clipboard",
    "Wyczyść": "Clear",
    "Wyczyścić?": "Clear?",
    "Wyczyść dziennik": "Clear the log",
    "Zdarzenia z nadajnika: włączone": "Sender events: on",
    "Zdarzenia z nadajnika: wyłączone": "Sender events: off",
    "połączenia padów z telefonem, Wake-on-LAN, błędy nadajnika": "pad connections to the phone, Wake-on-LAN, sender errors",
    "{pad} połączony": "{pad} connected",
    "{pad} rozłączony": "{pad} disconnected",
    "{pad}: pad {id}": "{pad}: pad {id}",
    "{pad}: brak sygnału przez {s} s - odłączony": "{pad}: no signal for {s} s - unplugged",
    "{pad}: przerwa w sygnale ponad {s} s": "{pad}: signal gap over {s} s",
    "{pad}: niska bateria ({pct}%)": "{pad}: low battery ({pct}%)",
    "{pad}: akcja mastera {action} nieudana: {err}": "{pad}: master action {action} failed: {err}",
    "{pad}: błąd wirtualnego pada: {err} (czy ViGEmBus jest zainstalowany?)":
        "{pad}: virtual pad error: {err} (is ViGEmBus installed?)",
    "HIDMaestro nieużywany - wirtualne pady przez ViGEmBus: {why}": "HIDMaestro not used - virtual pads via ViGEmBus: {why}",
    "tworzenie pada: {err}": "creating the pad: {err}",
    "Autostart ustawiony na uprawnienia administratora (HIDMaestro)": "Autostart set to administrator rights (HIDMaestro)",
    "Pady": "Pads",
    "Uruchom ponownie jako administrator": "Restart as administrator",
    "Stała kolejność graczy": "Fixed player order",
    "WIRTUALNE PADY": "VIRTUAL PADS",
    "Backend": "Backend",
    "HIDMaestro tworzy natywny DualSense (gry widzą pada PS5) sterownikiem w trybie użytkownika - jest rozwijany. "
    "Receiver pobiera go sam i uruchamia się jako administrator (tego wymaga tworzenie padów). ViGEmBus (DualShock 4) "
    "nie jest już rozwijany, zostaje jako zapas.":
        "HIDMaestro creates a native DualSense (games see a PS5 pad) with a user-mode driver - it is maintained. The "
        "receiver downloads it by itself and runs as administrator (creating pads requires it). ViGEmBus (DualShock 4) "
        "is no longer developed and stays as a fallback.",
    "Własna HIDMaestro.Core.dll (plik albo folder; puste = pobrana automatycznie)":
        "Own HIDMaestro.Core.dll (file or folder; empty = downloaded automatically)",
    "Pobieraj i aktualizuj HIDMaestro automatycznie (GitHub, raz na dobę)":
        "Download and update HIDMaestro automatically (GitHub, once a day)",
    "HIDMaestro: sprawdzanie / pobieranie…": "HIDMaestro: checking / downloading…",
    "HIDMaestro {v} pobrany": "HIDMaestro {v} downloaded",
    " (na GitHubie jest {v})": " ({v} is on GitHub)",
    "Pobieranie HIDMaestro: {err}": "HIDMaestro download: {err}",
    "automatycznie": "automatic",
    "Zainstaluj .NET 10": "Install .NET 10",
    "✓ .NET 10 zainstalowany - HIDMaestro zadziała przy następnym połączeniu pada":
        "✓ .NET 10 installed - HIDMaestro works on the next pad connection",
    "Instalacja .NET 10 nie udała się: {err}": ".NET 10 installation failed: {err}",
    "Instalowanie .NET 10 (winget)…": "Installing .NET 10 (winget)…",
    "Brakuje .NET 10 - HIDMaestro go wymaga.": ".NET 10 is missing - HIDMaestro needs it.",
    "HIDMaestro {v} pobrany - pady przejdą na DualSense przy następnym połączeniu":
        "HIDMaestro {v} downloaded - pads switch to DualSense on their next connection",
    "HIDMaestro {v} pobrany - zadziała po restarcie receivera": "HIDMaestro {v} downloaded - takes effect after a receiver restart",
    "Sprawdź teraz": "Check now",
    "KOLEJNOŚĆ GRACZY": "PLAYER ORDER",
    "Gry i Steam numerują pady wg kolejności podłączenia. Gdy pad 2 włączy się pierwszy, pad 1 dostaje najpierw pad "
    "zastępczy, więc pad 2 zawsze zostaje graczem 2. Który fizyczny pad to pad 1 / 2, pamięta nadajnik (slots.json, "
    "wg MAC). Zastępczy pad znika razem z padem 2.":
        "Games and Steam number pads in the order they connect. When pad 2 turns on first, pad 1 gets a placeholder pad "
        "first, so pad 2 always stays player 2. The sender remembers which physical pad is pad 1 / 2 (slots.json, by MAC). "
        "The placeholder goes away together with pad 2.",
    "Teraz: {kinds}": "Now: {kinds}",
    "Teraz: żaden wirtualny pad nie jest podłączony": "Now: no virtual pad is plugged in",
    "HIDMaestro nieużywany: {why}": "HIDMaestro not used: {why}",
    "Receiver działa bez uprawnień administratora - HIDMaestro nie utworzy padów.":
        "The receiver runs without administrator rights - HIDMaestro cannot create pads.",
    "Automatycznie: HIDMaestro, gdy jest, inaczej ViGEmBus": "Automatic: HIDMaestro when available, otherwise ViGEmBus",
    "HIDMaestro - DualSense (zapas: ViGEmBus)": "HIDMaestro - DualSense (fallback: ViGEmBus)",
    "ViGEmBus - DualShock 4": "ViGEmBus - DualShock 4",
    "Nadajnik online ({ip})": "Sender online ({ip})",
    "Nadajnik offline - brak pakietów od {s} s": "Sender offline - no packets for {s} s",
    "Błąd obsługi pakietu: {err}": "Packet handling error: {err}",
    "Master: {pad}": "Master: {pad}",
    "brak": "none",
    "Tryb konsoli: {what} - błąd: {err}": "Console mode: {what} - error: {err}",
    "Tryb konsoli: przełączenie pominięte - na pierwszym planie {exe}": "Console mode: switch skipped - {exe} in the foreground",
    "Dostępna aktualizacja - nowych zmian: {n}": "Update available - new changes: {n}",
    "Aktualizacja: {text}": "Update: {text}",
    "Nadajnik uruchomiony (wersja {arg})": "Sender started (version {arg})",
    "Pad {n} połączony z telefonem ({arg})": "Pad {n} connected to the phone ({arg})",
    "Pad {n} rozłączony od telefonu": "Pad {n} disconnected from the phone",
    "Pad {n} wyłączony przez nadajnik ({arg})": "Pad {n} switched off by the sender ({arg})",
    "Wysłano Wake-on-LAN do {arg} (pad {n})": "Wake-on-LAN sent to {arg} (pad {n})",
    "Wake-on-LAN nieudany: {arg}": "Wake-on-LAN failed: {arg}",
    "Nadajnik stracił kontakt z receiverem": "The sender lost contact with the receiver",
    "Sieć na telefonie niedostępna": "Network on the phone unavailable",
    "Błąd nadajnika: {arg}": "Sender error: {arg}",
    "Zdarzenie nadajnika {code}: {arg}": "Sender event {code}: {arg}",
    # ---------- lightbar ----------
    "Lightbar": "Lightbar",
    "NISKA BATERIA": "LOW BATTERY",
    "Próg niskiej baterii": "Low battery threshold",
    "Ten sam próg ma ostrzeżenie na karcie pada i powiadomienie z zasobnika. Ładujący się pad nie miga.":
        "The pad card warning and the tray notification use the same threshold. A charging pad doesn't blink.",
    "KOLOR LIGHTBARA": "LIGHTBAR COLOUR",
    "Poza grą (pulpit, Steam, launchery - lista w Trybie konsoli) zawsze świeci ten kolor. W grze może go zastąpić "
    "kolor gry, chyba że wyłączysz „Gra może zmienić kolor”. Miganie przy niskiej baterii działa zawsze, a potwierdzenia "
    "(np. złoty błysk mastera) są na wierzchu.":
        "Outside a game (desktop, Steam, launchers - the list in Console mode) this colour always shows. In a game the "
        "game's colour can replace it, unless you turn off \"A game can change the colour\". Low battery blinking always "
        "works, and confirmations (e.g. the master's gold flash) are on top.",
    "Aktualizacja nie odpowiada (git) - spróbuj ponownie albo uruchom update.bat":
        "The update isn't responding (git) - try again or run update.bat",
    "Gra może zmienić kolor (tylko w trakcie gry; wyłączone = zawsze ten kolor)":
        "A game can change the colour (only while playing; off = always this colour)",
    "{pad}: gra ustawiła kolor lightbara {hex} - ma pierwszeństwo przed kolorem z ustawień "
    "(Ustawienia → Lightbar → „Gra może zmienić kolor”)":
        "{pad}: the game set the lightbar colour {hex} - it takes priority over the colour from "
        "settings (Settings → Lightbar → \"A game can change the colour\")",
    "{pad}: program na PC ustawił kolor lightbara {hex} - poza grą świeci kolor z ustawień, w grze ten":
        "{pad}: a program on the PC set the lightbar colour {hex} - outside a game the colour from settings shows, in a "
        "game this one",
    "{pad}: program na PC ustawił kolor lightbara {hex} - pominięty (zawsze kolor z ustawień)":
        "{pad}: a program on the PC set the lightbar colour {hex} - ignored (always the colour from settings)",
    "Pad": "Pad",
    "Domyślne - pady bez własnego ustawienia": "Default - pads without their own setting",
    "Własne ustawienie dla tego pada (inaczej domyślne)": "Own setting for this pad (otherwise the default)",
    "Kolor": "Colour",
    "Stały kolor": "Solid colour",
    "Kolor wg stanu baterii": "Colour by battery level",
    "Wybierz kolor": "Pick a colour",
    "Kolor lightbara": "Lightbar colour",
    "Kolor domyślny (idle_lightbar z receiver.toml)": "Default colour (idle_lightbar from receiver.toml)",
    "  (domyślny)": "  (default)",
    "Miganie na czerwono przy niskiej baterii (także w grze)": "Blink red on low battery (also in games)",
    # ---------- Wake-on-LAN ----------
    "Wake-on-LAN": "Wake-on-LAN",
    "WAKE-ON-LAN": "WAKE-ON-LAN",
    "Budzenie komputera z pada": "Wake the computer from the pad",
    "Gdy komputer nie odpowiada (wyłączony / uśpiony), przytrzymanie tej kombinacji na padzie wysyła z telefonu magic "
    "packet. Przy działającym komputerze kombinacja idzie do receivera jak zwykle (np. pauza mastera), więc nic nie "
    "koliduje. Pad potwierdza wysłanie białym błyskiem, błąd - czerwonym.":
        "When the computer doesn't respond (off / asleep), holding this combination on the pad makes the phone send a "
        "magic packet. While the computer is running, the combination goes to the receiver as usual (e.g. the master's "
        "pause), so nothing clashes. The pad confirms with a white flash, an error with a red one.",
    "Kombinacja na padzie": "Combination on the pad",
    "Przytrzymanie": "Hold",
    "ADRESY": "ADDRESSES",
    "MAC karty sieciowej komputera": "Computer network card MAC",
    "Adres broadcast": "Broadcast address",
    "Puste = automatycznie: nadajnik zapamiętuje MAC komputera z tablicy ARP telefonu, póki komputer działa, i wysyła "
    "na broadcast swojej sieci. Komputer musi mieć włączony Wake-on-LAN w BIOS / UEFI i we właściwościach karty "
    "sieciowej (najlepiej przewodowej).":
        "Empty = automatic: the sender remembers the computer's MAC from the phone's ARP table while the computer is "
        "running, and sends to its network's broadcast. Wake-on-LAN must be enabled in the BIOS / UEFI and in the "
        "network card properties (preferably wired).",
    "auto: {v}": "auto: {v}",
    "Nadajnik nie podał jeszcze adresów (offline albo starsza wersja - Aktualizuj).":
        "The sender hasn't reported its addresses yet (offline or an older version - Update).",
    "Nadajnik użyje: MAC {mac}, broadcast {bcast}.": "The sender will use: MAC {mac}, broadcast {bcast}.",
    "nieznany - wpisz go albo uruchom receiver, gdy nadajnik działa": "unknown - enter it, or run the receiver while the sender is running",
    "Zły adres MAC - wpisz np. aa:bb:cc:dd:ee:ff albo zostaw puste.":
        "Invalid MAC address - enter e.g. aa:bb:cc:dd:ee:ff or leave it empty.",
    "Zły adres broadcast - wpisz np. 192.168.1.255 albo zostaw puste.":
        "Invalid broadcast address - enter e.g. 192.168.1.255 or leave it empty.",
    # ---------- widok Test ----------
    "Test": "Test",
    "Wynik = to, co dostaje gra po remapowaniu. Fizycznie = to, co robi sam pad. Test = przyciski, spusty, gałki, "
    "touchpad i ruch na żywo, dryf gałek, kalibracja żyroskopu.":
        "Result = what the game gets after remapping. Physical = what the pad itself does. Test = buttons, triggers, "
        "sticks, touchpad and motion live, stick drift, gyro calibration.",
    "Naciskaj przyciski, ruszaj gałkami, dotykaj touchpada - wszystko widać na żywo.":
        "Press buttons, move the sticks, touch the touchpad - everything shows live.",
    "Przytrzymaj ○, aby wyjść z testu.": "Hold ○ to leave the test.",
    "Blokuj wyjście do gry": "Block output to games",
    "Blokada wyjścia do gry": "Output to games blocked",
    "Na czas testu gra dostaje puszczone przyciski i gałki w środku tego pada; skróty pada, mysz touchpada i tryb "
    "konsoli też nie reagują. Działa tylko przy aktywnym oknie - po przejściu do gry albo wyjściu z testu pad od razu "
    "do niej wraca.":
        "During the test, games get this pad with released buttons and centred sticks; pad shortcuts, touchpad mouse "
        "and console mode don't react either. Only while this window is active - switching to the game or leaving the "
        "test gives the pad back right away.",
    "RUCH": "MOTION",
    "Ruch pada": "Pad motion",
    "Akcelerometr": "Accelerometer",
    "GAŁKI - DRYF": "STICKS - DRIFT",
    "Dryf gałek": "Stick drift",
    "Skala pierwiastkowa: małe odchyłki są powiększone. Kropkowane okręgi: 5% i 25% wychylenia; niebieski: strefa "
    "martwa z korekcji dryfu; żółty: szczyt ostatniego pomiaru.":
        "Square-root scale: small deviations are magnified. Dotted circles: 5% and 25% deflection; blue: deadzone from "
        "the drift correction; yellow: peak of the last measurement.",
    "Puść gałki i odłóż pad - pomiar trwa {s} s.": "Let go of the sticks and put the pad down - the measurement takes {s} s.",
    "Zmierz dryf": "Measure drift",
    "Mierzę…": "Measuring…",
    "Zastosuj": "Apply",
    "Zapisuje proponowaną strefę martwą jako korekcję dryfu tego pada - działa od razu we wszystkich jego profilach "
    "(także profilach gier).":
        "Saves the proposed deadzone as this pad's drift correction - it works right away in all of its profiles "
        "(game profiles too).",
    "Zastosowano: L {l}, R {r}": "Applied: L {l}, R {r}",
    "Korekcja dryfu usunięta": "Drift correction removed",
    "Usuwa korekcję dryfu tego pada": "Removes this pad's drift correction",
    "Korekcja dryfu: L {l} · R {r}": "Drift correction: L {l} · R {r}",
    "Bez korekcji dryfu - strefa martwa tylko z profilu.": "No drift correction - deadzone from the profile only.",
    "Brak próbek - pad niepodłączony?": "No samples - is the pad disconnected?",
    "Gałka się ruszała - puść obie gałki i zmierz jeszcze raz.": "A stick was moving - let go of both sticks and measure again.",
    "Lewa": "Left",
    "Prawa": "Right",
    "{s}: dryf {peak} → strefa martwa {dz}": "{s}: drift {peak} → deadzone {dz}",
    "Nadajnik w starszej wersji przycina odchyłki do ~2% - kliknij Aktualizuj, żeby mierzyć dokładnie.":
        "An older sender trims deviations up to ~2% - click Update to measure precisely.",
    "ŻYROSKOP - KALIBRACJA": "GYRO - CALIBRATION",
    "Kalibracja żyroskopu": "Gyro calibration",
    "Połóż pad na płaskiej, stabilnej powierzchni i nie dotykaj go przez {s} s.":
        "Put the pad on a flat, steady surface and don't touch it for {s} s.",
    "Kalibruj żyroskop": "Calibrate gyro",
    "Nadajnik liczy offset żyroskopu i zapisuje go na telefonie dla tego pada - po restarcie nie trzeba już kalibrować "
    "przy starcie.":
        "The sender measures the gyro offset and saves it on the phone for this pad - after a restart there's no need "
        "to calibrate at startup any more.",
    "Kalibruję - nie ruszaj pada…": "Calibrating - don't move the pad…",
    "Skalibrowano i zapisano na telefonie.": "Calibrated and saved on the phone.",
    "Skalibrowano (pad bez MAC - bez zapisu, do restartu nadajnika).":
        "Calibrated (pad without a MAC - not saved, until the sender restarts).",
    "Pad się ruszał - połóż go nieruchomo i spróbuj jeszcze raz.": "The pad was moving - put it down still and try again.",
    "Brak danych z czujnika ruchu pada.": "No data from the pad's motion sensor.",
    "Brak odpowiedzi nadajnika.": "No reply from the sender.",
    "Pad niepodłączony.": "Pad not connected.",
    "Kalibracja z okna wymaga nowszego nadajnika - kliknij Aktualizuj.":
        "Calibrating from the window needs a newer sender - click Update.",
    "Sprawdzam stan kalibracji…": "Checking calibration…",
    "Zapisana kalibracja z {date}.": "Saved calibration from {date}.",
    "Brak zapisanej kalibracji - nadajnik kalibruje pad przy starcie (pad musi wtedy leżeć).":
        "No saved calibration - the sender calibrates the pad at startup (it must lie still then).",
    "Offset teraz [°/s]: {v}": "Current offset [°/s]: {v}",
    "{pad}: żyroskop skalibrowany": "{pad}: gyro calibrated",
    "{pad}: kalibracja żyroskopu przerwana - pad się ruszał": "{pad}: gyro calibration aborted - the pad was moving",
    # ---------- widok Test: wyjście z pada ----------
    "{pad}: wyjście z widoku Test (Options + Create)": "{pad}: left the Test view (Options + Create)",
    "Wyjście z widoku Test z pada (Options + Create)": "Left the Test view from the pad (Options + Create)",
    "Skróty padów nie działają; Options + Create (pad master) kończy test.":
        "Pad shortcuts are off; Options + Create (master pad) ends the test.",
    # ---------- Bluetooth nadajnika (rx_bt, panel w oknie) ----------
    "Bluetooth nadajnika: brak danych (nadajnik offline albo starsza wersja - Aktualizuj)":
        "Sender Bluetooth: no data (sender offline or an older version - Update)",
    "Bluetooth nadajnika: brak danych od {s} s": "Sender Bluetooth: no data for {s} s",
    "Bluetooth nadajnika: {state}": "Sender Bluetooth: {state}",
    "połączone: {n}": "connected: {n}",
    "skanowanie {s} s": "scanning {s} s",
    "włączony": "on",
    "zablokowany (rfkill)": "blocked (rfkill)",
    "brak adaptera": "no adapter",
    "usługa bluetooth nie działa": "bluetooth service not running",
    "brak uprawnień (nadajnik musi działać jako root)": "no permission (the sender must run as root)",
    "brak busctl (systemd) na telefonie": "no busctl (systemd) on the phone",
    "błąd odczytu stanu": "cannot read the state",
    "Parowanie": "Pairing",
    "Łączenie": "Connecting",
    "Rozłączanie": "Disconnecting",
    "Usuwanie": "Removing",
    "Zasilanie Bluetooth": "Bluetooth power",
    "Zmiana kontrolera": "Controller change",
    "dongle USB": "USB dongle",
    "Kontroler Bluetooth": "Bluetooth controller",
    "Auto": "Auto",
    "Ręcznie": "Manual",
    "Wybrany": "Selected",
    "Zawsze kontroler wybrany przyciskiem Użyj (gdy go nie ma - jak Auto)":
        "Always the controller chosen with Use (if it is missing - like Auto)",
    "wewnętrzny USB": "internal USB",
    "wbudowany": "built-in",
    "Użyj": "Use",
    "używany": "in use",
    "wybrany": "selected",
    "Najlepszy kontroler: dongle USB przed wbudowanym (zwykle mniejsze opóźnienie), pozostałe wyłączone":
        "Best controller: USB dongle before built-in (usually lower latency), the others off",
    "Nadajnik nie zmienia kontrolerów - ustawiasz je sam (rfkill, bluetoothctl)":
        "The sender leaves controllers alone - set them yourself (rfkill, bluetoothctl)",
    "Dongle Bluetooth USB zwykle zmniejsza opóźnienie padów": "A USB Bluetooth dongle usually lowers pad latency",
    "Po zmianie kontrolera sparuj pady ponownie (każdy ma własny adres)":
        "After switching controllers pair the pads again (each has its own address)",
    "Skanowanie": "Scanning",
    "Operacja": "Operation",
    "sparowano i połączono": "paired and connected",
    "połączono": "connected",
    "rozłączono": "disconnected",
    "usunięto z telefonu": "removed from the phone",
    "gotowe": "done",
    "rozpoczęte": "started",
    "nie udało się": "failed",
    "brak odpowiedzi urządzenia": "no answer from the device",
    "telefon nie widzi urządzenia - uruchom skanowanie": "the phone can't see the device - start scanning",
    "parowanie odrzucone - włącz tryb parowania na padzie": "pairing rejected - put the pad in pairing mode",
    "brak uprawnień": "no permission",
    "Bluetooth telefonu wyłączony": "the phone's Bluetooth is off",
    "trwa inna operacja - spróbuj za chwilę": "another operation is running - try again in a moment",
    "BLUETOOTH NADAJNIKA": "SENDER BLUETOOTH",
    "Bluetooth nadajnika": "Sender Bluetooth",
    "Brak urządzeń - uruchom skanowanie i włącz tryb parowania na padzie.":
        "No devices - start scanning and put the pad in pairing mode.",
    "Kliknij: skanowanie, parowanie i łączenie padów z telefonem": "Click: scan, pair and connect pads with the phone",
    "Lista niedostępna - Bluetooth nadajnika nie działa albo nadajnik jest offline.":
        "List unavailable - the sender's Bluetooth isn't working or the sender is offline.",
    "Połącz": "Connect",
    "Rozłącz": "Disconnect",
    "Sparuj": "Pair",
    "Skanuj ({s} s)": "Scan ({s} s)",
    "Zatrzymaj skanowanie ({s} s)": "Stop scanning ({s} s)",
    "Sygnał widać w trakcie skanowania": "Signal strength is shown while scanning",
    "Tryb parowania DualSense: przytrzymaj Create + PS, aż lightbar zacznie szybko migać. Skan zabiera radio "
    "połączonym padom - kończy się sam po {s} s.":
        "DualSense pairing mode: hold Create + PS until the light bar blinks fast. Scanning takes radio time from "
        "connected pads - it stops by itself after {s} s.",
    "Usuwa parowanie z telefonu (np. pad sparowany potem z konsolą trzeba sparować od nowa)":
        "Removes the pairing from the phone (e.g. a pad later paired with a console must be paired again)",
    "Włącz Bluetooth": "Turn Bluetooth on",
    "Zamknij": "Close",
    "Zamknij panel (Esc, ○ na padzie)": "Close the panel (Esc, ○ on the pad)",
    "Zapomnij": "Forget",
    "Zapomnieć?": "Forget?",
    "nowy": "new",
    "połączony": "connected",
    "sparowany": "paired",
    # ---------- panel skrótów pada ----------
    '2 palce': '2 fingers',
    'Brak przypisanych akcji (Ustawienia → Master).': 'No actions assigned (Settings → Master).',
    'Czułość myszy touchpada +15 %': 'Touchpad mouse sensitivity +15 %',
    'Czułość myszy touchpada −15 %': 'Touchpad mouse sensitivity −15 %',
    'Działa tylko na pulpicie, w Steam i launcherach (lista w Ustawieniach), nie w grze.':
        'Works only on the desktop, in Steam and launchers (list in Settings), not in a game.',
    'Korona = tylko pad master (Ustawienia → Master).': 'Crown = master pad only (Settings → Master).',
    'Krótko: PS dla gry i Steam (po puszczeniu)': 'Short: PS for the game and Steam (on release)',
    'Mysz: lewy przycisk': 'Mouse: left button',
    'Mysz: lewy przycisk (fizyczny klik; 2 palce = prawy)': 'Mouse: left button (physical click; 2 fingers = right)',
    'Mysz: prawy przycisk (stuknięcie dwoma palcami)': 'Mouse: right button (two-finger tap)',
    'Mysz: przewijanie (ruch dwoma palcami)': 'Mouse: scrolling (two-finger move)',
    'Następny profil pada': 'Next pad profile',
    'PS dla gry i Steam (od razu)': 'PS for the game and Steam (immediately)',
    'Poprzedni / następny pad': 'Previous / next pad',
    'Poprzedni profil pada': 'Previous pad profile',
    'Poruszanie się, suwaki, zakładki': 'Move around, sliders, tabs',
    'Przełącz: mysz ↔ dotyk DS4 (klik touchpada)': 'Toggle: mouse ↔ DS4 touch (touchpad click)',
    'SKRÓTY PADA': 'PAD SHORTCUTS',
    'Skróty pada': 'Pad shortcuts',
    'Skróty z PS': 'PS shortcuts',
    'Skróty z PS są wyłączone (ps_modifier = false).': 'PS shortcuts are off (ps_modifier = false).',
    'Ten pad zostaje masterem (mignie na złoto)': 'This pad becomes the master (flashes gold)',
    'Touchpad': 'Touchpad',
    'Tryb TV (okno na pełnym ekranie i aktywne)': 'TV mode (window fullscreen and active)',
    'Tylko pad master': 'Master pad only',
    'W widoku Test pozostałe skróty nie działają.': 'In the Test view the other shortcuts are off.',
    'Widok Test': 'Test view',
    'Wstecz: zamknij panel, Ustawienia, wyjdź z pełnego ekranu': 'Back: close the panel, Settings, leave fullscreen',
    'Wszystkie skróty działają na każdym podłączonym padzie.': 'All shortcuts work on every connected pad.',
    'Wszystkie skróty pada w tym receiverze': 'All pad shortcuts of this receiver',
    'Wybór / kliknięcie': 'Select / click',
    'Wyjście awaryjne z testu (pad master; działa też przy schowanym oknie)':
        'Emergency exit from the test (master pad; works with the window hidden too)',
    'Wyjście z testu w trybie TV (testowany pad)': 'Leave the test in TV mode (pad under test)',
    'Włącz / wyłącz: ekran i dźwięk na TV (dwa naciśnięcia w {ms} ms)': 'On / off: screen and sound on the TV (two presses within {ms} ms)',
    'klik': 'click',
    'stuknięcie': 'tap',
    'Xbox Game Bar (Win+G)': 'Xbox Game Bar (Win+G)',
    'Steam Big Picture': 'Steam Big Picture',
    'Zrzut ekranu (Obrazy\\Zrzuty ekranu)': 'Screenshot (Pictures\\Screenshots)',
    'Zrzut ekranu przez Game Bar': 'Screenshot via Game Bar',
    # ---------- autostart, tryb konsoli przy starcie ----------
    "Autostart przeniesiony do Harmonogramu zadań - receiver startuje zaraz po zalogowaniu":
        "Autostart moved to Task Scheduler - the receiver starts right after logon",
    "Receiver uruchomiony {s} s po starcie Windows": "Receiver started {s} s after Windows start",
    "Receiver uruchomiony {s} s po zalogowaniu do Windows": "Receiver started {s} s after Windows logon",
    "Tryb konsoli czeka - na pierwszym planie {exe} (dodaj go do listy programów w Ustawieniach, jeśli to nie gra)":
        "Console mode waits - {exe} is in the foreground (add it to the program list in Settings if it is not a game)",
    "połączono pad master (pad {n}), po {s} s czekania": "master pad connected (pad {n}), after waiting {s} s",
    "Tryb konsoli: auto-włączenie pominięte - restart receivera przy wyłączonym trybie":
        "Console mode: auto-on skipped - receiver restarted with console mode off",
    "Tryb konsoli: błąd - {err}": "Console mode: error - {err}",
    "Tryb konsoli: Windows przywrócił oba ekrany - ponowne przełączenie na TV":
        "Console mode: Windows restored both screens - switching to the TV again",
}
