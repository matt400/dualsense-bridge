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
    # ---------- okno główne ----------
    "Wynik": "Result",
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
    "DS4: {ds4}  ·  touchpad: {tp}": "DS4: {ds4}  ·  touchpad: {tp}",
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
    "w grze podwójne kliknięcie nic nie robi, a włączenie po połączeniu mastera czeka na wyjście z gry.":
        "Switching works only while one of these programs is in the foreground (one per line) - in a game the "
        "double press does nothing and turning on after the master connects waits until you leave the game.",
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
    "Włącz po połączeniu pada master": "Turn on when the master pad connects",
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
    "Nadajnik nie ma dostępu do wibracji - uruchom go przez doas.": "The sender has no access to rumble - run it with doas.",
    "sterownik": "driver",
    "wibracja: {ways}": "rumble: {ways}",
    # ---------- ikony ----------
    "Ekran": "Screen",
    "Uruchom ponownie": "Restart",
}
