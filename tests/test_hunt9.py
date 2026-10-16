"""Receiver: regresje z dziewiątego przeglądu - stan skrótów, akcji mastera i przycisku trybu konsoli na granicy widoku
Test / blokady wyjścia liczony od wejść trzymanych w tej chwili (a nie "jak po połączeniu pada").
    python3 tests/test_hunt9.py receiver"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_console
import rx_hotkeys as Hk
import rx_master
import rx_profiles as P
import rx_testmode as TM
import rx_winapi as Wapi
from rx_buttons import BUTTON_CODES
from rx_console import Console, ConsoleConfig

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

PS, SHARE, CROSS, OPTIONS = (BIT[BUTTON_CODES[n]] for n in ("ps", "share", "cross", "options"))

def game_mask():
    with S.lock:
        return S.output(0)["mask"]

def keys():
    return [c[1:] for c in calls if c[0] == "k"]

def neutral():
    """Pad połączony, nic nie trzymane, widok Test zamknięty, sloty skrótów po pierwszym stanie."""
    TM.set_active(False); P.block(0, False)
    state(0, mask=CROSS); state(0); time.sleep(Hk.TAP_S + 0.05)
    calls.clear()


# ---------- 1. pierwsze PS po wyjściu z widoku Test trafia do gry ----------
neutral()
TM.set_active(True); TM.set_active(False)          # np. okno Test straciło fokus (klik w grę)
state(0, mask=PS)
check(not Hk.slots[0].used, "świeże PS po teście to nie 'PS trzymane od włączenia pada'")
state(0)
check(game_mask() & PS, "puszczone PS -> impuls PS dla gry (wcześniej połknięty)")
time.sleep(Hk.TAP_S + 0.05)
check(not game_mask() & PS, "impuls PS się kończy")

# ---------- 2. to samo po zdjęciu blokady wyjścia (P.block, wątek okna) ----------
neutral()
P.block(0, True); P.block(0, False)
state(0, mask=PS); state(0)
check(game_mask() & PS, "pierwsze PS po zdjęciu blokady trafia do gry")
time.sleep(Hk.TAP_S + 0.05)

# ---------- 3. PS trzymane przez granicę testu + Share = zrzut ekranu, bez Share i PS w grze ----------
neutral()
TM.set_active(True)
state(0, mask=PS)                                  # PS wciśnięte w teście (skróty stoją)
TM.set_active(False)                               # wyjście z testu, PS dalej trzymane
state(0, mask=PS | SHARE)
time.sleep(0.1)                                    # akcja skrótu w osobnym wątku
check((Wapi.VK_LWIN, Wapi.VK_SNAPSHOT) in keys(), "PS (trzymane przez granicę) + Share -> zrzut ekranu")
check(not game_mask() & SHARE, "Share z kombinacji nie trafia do gry (wcześniej: Share w grze, bez zrzutu)")
state(0, mask=PS); state(0)
check(not game_mask() & PS, "PS trzymane przez granicę i użyte w skrócie - bez impulsu PS dla gry")
time.sleep(Hk.TAP_S + 0.05)

# ---------- 4. PS trzymane przez granicę i puszczone bez niczego - bez impulsu i bez Game Bar ----------
neutral()
TM.set_active(True); state(0, mask=PS); TM.set_active(False)
state(0, mask=PS, lt=200); state(0)
time.sleep(0.05)
check(not game_mask() & PS and not keys(), "PS trzymane przez granicę testu - nic nie trafia do gry ani do Windows")

# ---------- 5. połączenie pada dalej "jak po połączeniu": PS trzymane od włączenia nie idzie do gry ----------
neutral()
Hk.reset(0); rx_master.reset(0); N._digital[0] = None
state(0, mask=PS); state(0)
check(not game_mask() & PS, "reset bez keep (nowy pad): PS trzymane od włączenia nie daje impulsu")

# ---------- 6. master: PS trzymane przez granicę + Options = akcja (pauza) od razu ----------
neutral()
TM.set_active(True); state(0, mask=PS); TM.set_active(False)
state(0, mask=PS | OPTIONS)
time.sleep(0.1)
check((Wapi.VK_ESCAPE,) in keys(), "PS + Options po teście -> pauza mastera")
check(not game_mask() & OPTIONS, "Options z kombinacji nie trafia do gry")
state(0, mask=PS); state(0); time.sleep(Hk.TAP_S + 0.05)

# ---------- 7. tryb konsoli: R2 puszczony w teście nie gubi pierwszego kliknięcia po teście ----------
log = []
rx_console._display = lambda mode: log.append(("display", mode))
rx_console._audio = lambda name: log.append(("audio", name))
rx_console._toast = lambda *a: log.append(("toast",))
rx_console._tv_mode = lambda *a: None
c = Console(ConsoleConfig(toggle_button="r2", allowed_apps=())); c.start()
done = threading.Event(); c._jobs.put(done.set); done.wait(5)
rx_console.ctl = c
neutral()
state(0, rt=255)                                   # R2 wciśnięty przed testem
c._last_click[0] = 0.0                             # to naciśnięcie nie jest częścią serii poniżej
TM.set_active(True)
state(0, rt=0)                                     # puszczony w teście (tryb konsoli go nie widzi)
TM.set_active(False)
state(0, rt=255); state(0, rt=0); state(0, rt=255); state(0, rt=0)
end = time.monotonic() + 3
while not c.on and time.monotonic() < end:
    time.sleep(0.01)
check(c.on, "podwójne R2 zaraz po teście przełącza tryb konsoli (wcześniej pierwsze kliknięcie ginęło)")
c.close(); rx_console.ctl = None
neutral()

if fails:
    print("FAIL", len(fails), "błędów")
    sys.exit(1)
print("WSZYSTKO OK")
