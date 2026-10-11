"""Tryb konsoli (rx_console): config, podwójne kliknięcie, ON/OFF, auto-OFF, start, błędy poleceń, wybór przycisku."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_audio, rx_console
from rx_console import Console, ConsoleConfig

PATH = os.path.join(TMP, "console.json"); C.CONSOLE_PATH = PATH
ok = lambda msg: print("OK  ", msg)
fails = []
def check(cond, msg):
    if not cond:
        fails.append(msg); print("FAIL", msg)

# ---------- polecenia systemowe zastąpione rejestrem ----------
calls = []
rx_console._display = lambda mode: calls.append(("display", mode))
rx_console._audio = lambda name: calls.append(("audio", name))
rx_console._toast = lambda title, msg="": calls.append(("toast", title, msg))
rx_console._tv_mode = lambda w, h, sc: calls.append(("tv", w, h, sc))

def wait_for(pred, timeout=2.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()

def new_console(**kw):
    """Console z wątkiem roboczym; startup (extend) już przetworzony i wyczyszczony z rejestru.
    Testy kliknięć używają PS (trudniejszy przypadek: PS jest też modyfikatorem skrótów)."""
    kw.setdefault("toggle_button", "ps")
    c = Console(ConsoleConfig(**kw)); calls.clear(); c.start()
    wait_for(lambda: ("display", c.cfg.startup_mode) in calls); calls.clear()
    rx_console.ctl = c
    return c

def click(i=0, button=316, gap=0.05):
    state(i, mask=BIT[button]); time.sleep(gap); state(i, mask=0)

PS, SHARE = 316, 314

# ---------- config: brak pliku, błędne wartości, uszkodzony plik ----------
cfg = rx_console.load(PATH)
check(cfg == ConsoleConfig() and os.path.exists(PATH), "brak pliku -> domyślne i utworzony console.json")
check(json.load(open(PATH)) == {"toggle_button": "r2", "double_click_ms": 400, "audio_tv": "", "audio_pc": "",
                                "timeout_s": 60, "startup_mode": "extend", "tv_width": 2560, "tv_height": 1440,
                                "tv_scale": 200, "auto_on_master": True, "allowed_apps": list(rx_console.DEFAULT_APPS),
                                "version": 3}, "domyślne wartości w pliku (R2, TV 1440p / 200 %)")
json.dump({"toggle_button": "ps", "double_click_ms": 300, "audio_tv": "", "audio_pc": "", "timeout_s": 60,
           "startup_mode": "extend"}, open(PATH, "w"))
cfg = rx_console.load(PATH)
check(cfg.toggle_button == "r2" and cfg.double_click_ms == 300 and json.load(open(PATH))["version"] == 3,
      f"stary plik (bez wersji) z PS -> R2, reszta zostaje: {cfg}")
json.dump({"toggle_button": "mute", "version": 2}, open(PATH, "w"))
check(rx_console.load(PATH).toggle_button == "r2", "wersja 2 z mute (usuniętym) -> R2")
json.dump({"toggle_button": "ps", "version": 2}, open(PATH, "w"))
check(rx_console.load(PATH).toggle_button == "ps", "PS wybrany świadomie w wersji 2 zostaje")
json.dump({**json.load(open(PATH)), "toggle_button": "ps"}, open(PATH, "w"))
check(rx_console.load(PATH).toggle_button == "ps", "PS wybrany świadomie w nowym pliku zostaje")
json.dump({"toggle_button": "share", "double_click_ms": 250, "audio_tv": " LG TV ", "audio_pc": "Głośniki",
           "timeout_s": 90, "startup_mode": "internal"}, open(PATH, "w"))
cfg = rx_console.load(PATH)
check(cfg == ConsoleConfig("share", 250, "LG TV", "Głośniki", 90, "internal"), f"poprawny plik wczytany: {cfg}")
json.dump({"toggle_button": "nie-ma", "double_click_ms": "szybko", "timeout_s": True, "startup_mode": "tv", "audio_tv": 5}, open(PATH, "w"))
cfg = rx_console.load(PATH)
check(cfg == ConsoleConfig(), "same błędne wartości -> domyślne")
json.dump({"toggle_button": "cross", "double_click_ms": 99999, "timeout_s": 120}, open(PATH, "w"))
cfg = rx_console.load(PATH)
check(cfg.toggle_button == "cross" and cfg.timeout_s == 120 and cfg.double_click_ms == 400, "błędna wartość zastąpiona, dobre zostają")
check(json.load(open(PATH))["double_click_ms"] == 400 and "startup_mode" in json.load(open(PATH)), "plik poprawiony i uzupełniony o brakujące klucze")
open(PATH, "w").write("{ to nie jest json")
cfg = rx_console.load(PATH)
check(cfg == ConsoleConfig() and os.path.exists(PATH + ".bad") and json.load(open(PATH)), "uszkodzony plik -> domyślne, stary jako .bad")
open(PATH, "w").write("[1, 2]")
check(rx_console.load(PATH) == ConsoleConfig(), "plik z listą zamiast obiektu -> domyślne")
rx_console.save(ConsoleConfig("l2", 300, "A", "B", 45, "extend"), PATH)
check(rx_console.load(PATH) == ConsoleConfig("l2", 300, "A", "B", 45, "extend") and not os.path.exists(PATH + ".tmp"), "zapis i odczyt, bez plików tymczasowych")
ok("config: domyślne, poprawki, uszkodzony plik, zapis atomowy")

# ---------- wykrywanie przycisków ----------
det = rx_console.detector
check(det("ps")(BIT[PS], 0, 0, 0, 0) and not det("ps")(BIT[SHARE], 0, 0, 0, 0), "przycisk")
check(det("dpad_up")(0, 0, -1, 0, 0) and not det("dpad_up")(0, 0, 1, 0, 0) and det("dpad_left")(0, -1, 0, 0, 0), "d-pad")
check(det("r2")(0, 0, 0, 0, 200) and not det("r2")(0, 0, 0, 0, 10) and det("l2")(0, 0, 0, 255, 0), "spusty")
ok("detector: przyciski, d-pad, spusty")

# ---------- podwójne kliknięcie przełącza ON/OFF (przez prawdziwy rx_net.handle) ----------
c = new_console(audio_tv="LG TV", audio_pc="Głośniki", double_click_ms=400)
click(); time.sleep(0.05); click()
check(wait_for(lambda: c.on), "podwójne kliknięcie PS -> ON")
check(calls == [("display", "tv"), ("tv", 2560, 1440, 200), ("audio", "LG TV"), ("toast", "Tryb konsoli włączony", "")],
      f"ON: ekran, rozdzielczość i skala TV, dźwięk TV, toast: {calls}")
check(S.states[0]["raw"][0] == 0, "stan pada dalej śledzony (przyciski trafiają do gry)")
calls.clear()
click(); time.sleep(0.05); click()
check(wait_for(lambda: not c.on), "drugie podwójne kliknięcie -> OFF")
check(calls == [("display", "extend"), ("audio", "Głośniki"), ("toast", "Tryb konsoli wyłączony", "")], f"OFF: ekran, dźwięk PC, toast: {calls}")
ok("podwójne kliknięcie: ON (TV + audio_tv + toast), OFF (extend + audio_pc + toast)")

calls.clear()
click(); time.sleep(0.5); click(); time.sleep(0.5); click()
time.sleep(0.3)
check(not c.on and not calls, "kliknięcia rzadsze niż double_click_ms nic nie przełączają")
calls.clear(); time.sleep(0.5)        # poprzednie kliknięcie ma być poza oknem
click(); time.sleep(0.05); click(); time.sleep(0.05); click()
check(wait_for(lambda: c.on), "seria kliknięć przełącza")
time.sleep(0.4)
check(len([x for x in calls if x[0] == "toast"]) == 1, f"trzy szybkie kliknięcia = jedno przełączenie: {calls}")
ok("rzadkie kliknięcia ignorowane, seria nie przełącza dwa razy")

# inny pad: dwa kliknięcia na różnych padach to nie podwójne kliknięcie
c.on = False; calls.clear()
click(0); click(1)
time.sleep(0.3)
check(not calls, "kliknięcie na dwóch różnych padach nie liczy się jako podwójne")
# inny przycisk niż skonfigurowany
click(0, SHARE); click(0, SHARE); time.sleep(0.3)
check(not calls, "podwójne kliknięcie innego przycisku nic nie robi")
ok("kliknięcia na różnych padach / innym przycisku ignorowane")

# ---------- sieć nie czeka na polecenia ekranu ----------
c.on = False; calls.clear()
rx_console._display = lambda mode: (time.sleep(0.6), calls.append(("display", mode)))
t0 = time.monotonic(); click(gap=0.0); click(gap=0.0)
check(time.monotonic() - t0 < 0.3, f"obsługa pakietów nie czeka na DisplaySwitch ({time.monotonic() - t0:.3f} s)")
check(wait_for(lambda: ("display", "tv") in calls, 3), "polecenie i tak się wykonuje w tle")
rx_console._display = lambda mode: calls.append(("display", mode))
ok("pętla odbioru nie jest blokowana (polecenia w osobnym wątku)")
c.close(restart=True)

# ---------- auto-OFF po ciszy ----------
c = new_console(audio_tv="LG TV", audio_pc="Głośniki", timeout_s=5)
c._apply(True); calls.clear()
now = time.monotonic()
for i in range(C.NUM_PADS):
    S.last_seen[i] = now
c._tick(); time.sleep(0.1)
check(c.on and not calls, "pakiety świeże -> zostaje ON")
for i in range(C.NUM_PADS):
    S.last_seen[i] = now - 6
c._since = now - 6
c._tick()
check(not c.on and calls[:2] == [("display", "extend"), ("audio", "Głośniki")] and calls[2][0] == "toast"
      and calls[2][1] == "Tryb konsoli wyłączony" and "5 s" in calls[2][2], f"cisza > timeout_s -> OFF + toast: {calls}")
calls.clear(); c._tick()
check(not calls, "w trybie OFF cisza nic nie robi")
c._apply(True); c._since = time.monotonic(); calls.clear()
for i in range(C.NUM_PADS):
    S.last_seen[i] = 0.0
c._tick()
check(c.on and not calls, "ręcznie włączony tryb nie wyłącza się od razu (liczy się od chwili włączenia)")
c.close(restart=True)
ok("auto-OFF: po timeout_s ciszy, nie wcześniej")

# ---------- start: extend / internal, dźwięk PC ----------
for mode in ("extend", "internal"):
    calls.clear(); cc = Console(ConsoleConfig(audio_pc="Głośniki", startup_mode=mode)); cc.start()
    check(wait_for(lambda: ("audio", "Głośniki") in calls) and calls == [("display", mode), ("audio", "Głośniki")] and not cc.on,
          f"start {mode}: {calls}")
    cc.close(restart=True)
calls.clear(); cc = Console(ConsoleConfig(startup_mode="extend")); cc.start(); wait_for(lambda: calls)
time.sleep(0.2); check(calls == [("display", "extend")], f"pusty audio_pc -> dźwięk bez zmian: {calls}"); cc.close(restart=True)
ok("start: DisplaySwitch /extend lub /internal + audio_pc")

# ---------- błędy poleceń nie wywalają skryptu ----------
def boom(*a, **k): raise OSError("brak polecenia")
rx_console._display, rx_console._audio, rx_console._toast, rx_console._tv_mode = boom, boom, boom, boom
c = new_console(audio_tv="X", audio_pc="Y"); calls.clear()
c._jobs.put(c._toggle)
check(wait_for(lambda: c.on), "wszystkie polecenia rzucają wyjątek, a tryb i tak się przełącza")
c._jobs.put(c._toggle)
check(wait_for(lambda: not c.on) and c._thread.is_alive(), "wątek roboczy żyje po błędach")
rx_console._display = lambda mode: calls.append(("display", mode))
rx_console._audio = lambda name: calls.append(("audio", name))
rx_console._toast = lambda title, msg="": calls.append(("toast", title, msg))
c._jobs.put(c._toggle)
check(wait_for(lambda: ("display", "tv") in calls and ("audio", "X") in calls), "po naprawie polecenia działają dalej")
rx_console._tv_mode = lambda w, h, sc: calls.append(("tv", w, h, sc))
c.close(restart=True)
ok("błędy ekranu / dźwięku / toastu logowane, nic się nie wywala")

# ---------- wybór przycisku na padzie ----------
c = new_console(); got = []
state(0, mask=BIT[SHARE])                                  # trzymany w chwili uzbrojenia - nie liczy się
c.arm_capture(got.append)
state(0, mask=BIT[SHARE] | BIT[PS]);
check(got == ["ps"], f"przechwycony nowo wciśnięty przycisk (a nie trzymany): {got}")
state(0, mask=0); calls.clear(); c.on = False
click(0, PS); click(0, PS)
check(wait_for(lambda: c.on) and got == ["ps"], "po przechwyceniu callback jednorazowy, a podwójne kliknięcie znów działa")
c.on = False; calls.clear()
c.arm_capture(got.append); click(0, PS); click(0, PS); time.sleep(0.3)
check(len(got) == 2 and not calls and not c.on, "podczas wyboru przycisku podwójne kliknięcie nie przełącza trybu")
c.arm_capture(got.append); c.cancel_capture(); state(0, mask=BIT[SHARE]); state(0, mask=0)
check(len(got) == 2, "anulowany wybór nic nie przechwytuje")
c.arm_capture(got.append); state(0, hat=(0, -1)); state(0)
check(got[-1] == "dpad_up", f"d-pad też można wybrać: {got}")
c.arm_capture(got.append); state(0, rt=200); state(0)
check(got[-1] == "r2", f"spust też: {got}")
c.close(restart=True)
ok("wybór przycisku: pierwszy nowo wciśnięty, jednorazowo, bez przełączania trybu")

# ---------- Przetestuj: ON z podanym configiem, potem samo OFF ----------
rx_console.TEST_S = 0.4
c = new_console(audio_tv="stare TV", audio_pc="stare PC"); calls.clear()
c.test(ConsoleConfig(audio_tv="nowe TV", audio_pc="nowe PC"))
check(wait_for(lambda: ("audio", "nowe TV") in calls) and c.on, "test: ON z wartościami z okna")
check(wait_for(lambda: ("audio", "nowe PC") in calls, 4), "test: po czasie OFF z tymi samymi wartościami")
check(not c.on and ("audio", "stare TV") not in calls, "test nie używa zapisanej konfiguracji")
c.close(restart=True)
ok("Przetestuj: ON i automatyczny powrót, na ustawieniach z okna")

# ---------- zamykanie: ON -> powrót na monitor (bez toastu); restart nie przełącza ----------
c = new_console(audio_pc="Głośniki"); c._apply(True); calls.clear()
c.close(restart=False)
check(("display", "extend") in calls and ("audio", "Głośniki") in calls and not any(x[0] == "toast" for x in calls) and not c.on,
      f"wyjście z włączonym trybem -> extend, bez toastu: {calls}")
c = new_console(audio_pc="Głośniki"); c._apply(True); calls.clear()
c.close(restart=True)
check(not calls, "przy restarcie ekran zostawia start nowego procesu")
ok("zamknięcie: ON wraca do extend, restart nie dubluje przełączeń")

# ---------- skróty z PS to nie kliknięcia ----------
from proto import T_CLICK
c = new_console(); calls.clear()
def combo(second, gap=0.03):
    state(0, mask=BIT[PS]); time.sleep(gap); second(); time.sleep(gap); state(0, mask=0); time.sleep(gap)
for _ in range(2):                                         # PS + Share (zrzut ekranu) dwa razy pod rząd
    combo(lambda: state(0, mask=BIT[PS] | BIT[SHARE]))
time.sleep(0.2)
check(not c.on and not any(x[0] == "display" for x in calls), f"PS + Share dwa razy nie przełącza trybu: {calls}")
for _ in range(2):                                         # PS + d-pad (zmiana profilu) dwa razy
    combo(lambda: state(0, mask=BIT[PS], hat=(1, 0)))
time.sleep(0.2)
check(not c.on, "PS + d-pad dwa razy nie przełącza trybu")
for _ in range(2):                                         # PS + klik touchpada (mysz / dotyk) dwa razy
    combo(lambda: (feed(T_CLICK, 0, 1), feed(T_CLICK, 0, 0)))
time.sleep(0.2)
check(not c.on, "PS + klik touchpada dwa razy nie przełącza trybu")
for m in (BIT[SHARE], BIT[SHARE] | BIT[PS], BIT[SHARE], BIT[SHARE] | BIT[PS], BIT[SHARE], 0):
    state(0, mask=m); time.sleep(0.03)
time.sleep(0.2)
check(not c.on, "PS naciskany przy trzymanym Share to też skrót")
time.sleep(0.5)
click(); time.sleep(0.05); click()
check(wait_for(lambda: c.on), "zwykłe podwójne kliknięcie po skrótach dalej działa")
c.close(restart=True)
ok("skróty z PS (Share, d-pad, touchpad) nie liczą się jako kliknięcia")

# ---------- rozdzielczość / skala TV ----------
c = new_console(tv_width=0, tv_height=0, tv_scale=0); calls.clear()
c._apply(True); c._apply(False)
check(not any(x[0] == "tv" for x in calls), f"0 = bez zmiany rozdzielczości i skali: {calls}")
c.close(restart=True)
c = new_console(tv_width=3840, tv_height=2160, tv_scale=0); calls.clear()
c._apply(True); c._apply(False)
check([x for x in calls if x[0] == "tv"] == [("tv", 3840, 2160, 0)], f"tylko przy ON, z wartościami z configu: {calls}")
c.close(restart=True)
json.dump({"tv_width": 2560, "tv_height": 1440, "tv_scale": 130, "version": 2}, open(PATH, "w"))
check(rx_console.load(PATH).tv_scale == 200, "skala spoza kroków Windows -> domyślna")
import rx_display as D
check(D.pick_mode([(2560, 1440, 32, 60), (2560, 1440, 32, 120), (1920, 1080, 32, 144), (2560, 1440, 16, 144)], 2560, 1440)
      == (2560, 1440, 32, 120) and D.pick_mode([(1920, 1080, 32, 60)], 2560, 1440) is None, "wybór trybu: 32 bpp, najwyższe Hz")
check(D.scale_relative(200, -2, 0, 7) == (2, False) and D.scale_relative(150, -2, 0, 7) == (0, True),
      "skala względem zalecanej (zalecana 150 %: 200 % = +2)")
try:
    D.scale_relative(500, -1, 0, 3); check(False, "skala poza zakresem ekranu -> błąd")
except ValueError as e:
    check("not supported" in str(e), str(e))
steps = iter([[1, 2], [1, 2], [("luid", 3, "DISPLAY2")]])      # Windows przełącza układ ekranów z opóźnieniem
D.active_sources = lambda: next(steps)
D.set_resolution = lambda dev, w, h: f"{w}x{h}"
D.set_scale = lambda a, sid, p: f"scale {p}%"
sleep = D.time.sleep
D.time.sleep = lambda s: None
check(D.apply_tv(2560, 1440, 200, settle_s=5) == "DISPLAY2: 2560x1440, scale 200%", "apply_tv: czeka na jeden ekran, potem tryb i skala")
D.active_sources = lambda: [1, 2]
try:
    D.apply_tv(2560, 1440, 200, settle_s=0); check(False, "dwa ekrany -> nie zmienia")
except RuntimeError:
    pass
D.time.sleep = sleep                                             # moduł time jest wspólny - przywróć
ok("TV: rozdzielczość i skala tylko przy ON, wybór trybu, skala względna, czekanie na ekran")

# ---------- R2 jako przycisk trybu (domyślny): histereza, kombinacje ----------
c = new_console(toggle_button="r2"); calls.clear()
def r2(v, mask=0):
    state(0, rt=v, mask=mask); time.sleep(0.03)
r2(255); r2(0); r2(255); r2(0)
check(wait_for(lambda: c.on), "podwójne pełne R2 -> ON")
c.on = False; calls.clear(); time.sleep(0.5)
for v in (100, 200, 120, 199, 90, 0):                      # drżenie wokół progu to jedno naciśnięcie (histereza)
    r2(v)
time.sleep(0.3)
check(not c.on, "drżenie spustu przy progu nie jest podwójnym kliknięciem")
time.sleep(0.5)
for v in (60, 150, 0, 70, 0):                               # lekkie naciśnięcia (gra) nie liczą się
    r2(v)
time.sleep(0.3)
check(not c.on, "lekkie naciśnięcia R2 nie przełączają")
r2(255, BIT[304]); r2(0); r2(255, BIT[304]); r2(0); time.sleep(0.2)
check(not c.on, "R2 z innym przyciskiem (w grze) to nie kliknięcie")
c.close(restart=True)
ok("R2: podwójne głębokie naciśnięcie przełącza, drżenie / lekkie / z innym przyciskiem nie")

# ---------- auto-ON po połączeniu pada master, ręczne wyłączenie, restart ----------
import rx_master, rx_settings
from dataclasses import replace
from proto import T_IDENT, T_BYE
MAC = "aa:bb:cc:00:00:01"
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad=MAC)))
c = new_console(toggle_button="r2"); calls.clear()
feed(T_IDENT, 1, MAC.encode())
check(wait_for(lambda: c.on) and any(x[0] == "toast" and "master" in x[2] for x in calls), f"master połączony -> ON: {calls}")
time.sleep(0.5)
for v in (255, 0, 255, 0):
    state(1, rt=v); time.sleep(0.03)
check(wait_for(lambda: not c.on), "podwójne R2 na masterze -> ręcznie OFF")
time.sleep(0.3); feed(T_IDENT, 1, MAC.encode()); time.sleep(0.3)
check(not c.on, "ten sam pad dalej połączony (ident bez zmian) - tryb zostaje wyłączony")
feed(T_BYE, 1); time.sleep(1.1); feed(T_IDENT, 1, MAC.encode())
check(wait_for(lambda: c.on), "ponowne połączenie mastera -> znów ON")
c.close(restart=True)
c = new_console(toggle_button="r2", auto_on_master=False); feed(T_BYE, 1); time.sleep(1.1)
feed(T_IDENT, 1, MAC.encode()); time.sleep(0.3)
check(not c.on, "auto_on_master = false -> bez automatu")
c.close(restart=True)
feed(T_IDENT, 0, b"cc:cc:cc:00:00:09"); time.sleep(0.2)
c = new_console(toggle_button="r2"); feed(T_BYE, 0); time.sleep(1.1); feed(T_IDENT, 0, b"cc:cc:cc:00:00:09"); time.sleep(0.3)
check(not c.on, "zwykły pad (nie master) nie włącza trybu")
c.close(restart=True)
os.environ[rx_console.RESTART_ENV] = "0"
c = new_console(toggle_button="r2"); feed(T_BYE, 1); time.sleep(1.1); feed(T_IDENT, 1, MAC.encode()); time.sleep(0.3)
check(not c.on and rx_console.RESTART_ENV not in os.environ, "restart z wyłączonym trybem: powrót mastera go nie włącza")
c.close(restart=True)
os.environ[rx_console.RESTART_ENV] = "1"
calls.clear(); c = Console(ConsoleConfig()); c.start()
check(wait_for(lambda: c.on) and ("display", "tv") in calls, f"restart z włączonym trybem: od razu ON: {calls}")
c.close(restart=True)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="")))
ok("auto-ON: połączenie mastera, ręczne OFF, ponowne połączenie, wyłączalne, restart zachowuje stan")

# ---------- gra na pierwszym planie: przełączanie zablokowane, auto-ON czeka ----------
import rxenv
pulses = []
F.pulse = lambda i, kind, force=False: pulses.append((i, kind))
c = new_console(toggle_button="r2"); calls.clear()
rxenv.FG[0] = "ittakestwo.exe"
for v in (255, 0, 255, 0):
    state(0, rt=v); time.sleep(0.03)
time.sleep(0.3)
check(not c.on and (0, "denied") in pulses, f"gra na pierwszym planie: podwójne R2 nic nie robi, czerwone mignięcie: {pulses}")
rxenv.FG[0] = "steamwebhelper.exe"; time.sleep(0.5)
for v in (255, 0, 255, 0):
    state(0, rt=v); time.sleep(0.03)
check(wait_for(lambda: c.on), "Steam / Big Picture na pierwszym planie: przełącza")
c.close(restart=True)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad=MAC)))
c = new_console(toggle_button="r2"); calls.clear()
rxenv.FG[0] = "ittakestwo.exe"
feed(T_BYE, 1); time.sleep(1.1); feed(T_IDENT, 1, MAC.encode())
for _ in range(15):
    state(1); time.sleep(0.1)                               # pad żyje, gra dalej na pierwszym planie
check(not c.on and c._pending == 1, "auto-ON w trakcie gry czeka")
rxenv.FG[0] = "explorer.exe"
for _ in range(15):
    state(1); time.sleep(0.1)
check(c.on, "gra zamknięta (pulpit na pierwszym planie) -> auto-ON")
c.close(restart=True)
c = new_console(toggle_button="r2", allowed_apps=("explorer.exe",)); rxenv.FG[0] = "steam.exe"
for v in (255, 0, 255, 0):
    state(0, rt=v); time.sleep(0.03)
time.sleep(0.3)
check(not c.on, "lista dozwolonych z ustawień (steam.exe usunięty) obowiązuje")
c.close(restart=True)
rxenv.FG[0] = None
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="")))
ok("gra na pierwszym planie: R2 zablokowane, auto-ON czeka do wyjścia z gry, lista z ustawień")

# ---------- rx_audio.find ----------
devs = [("1", "Speakers (Realtek)"), ("2", "LG TV (NVIDIA High Definition Audio)"), ("3", "Headphones (USB)")]
check(rx_audio.find("speakers (realtek)", devs) == "1", "dokładna nazwa, bez wielkości liter")
check(rx_audio.find("LG TV", devs) == "2", "jedyne urządzenie zawierające tekst")
check(rx_audio.find("(", devs) is None and rx_audio.find("nie ma", devs) is None, "niejednoznaczne / brak -> None")
ok("rx_audio.find")

print("FAIL" if fails else "PASS", "console:", len(fails), "błędów" if fails else "wszystko OK")
sys.exit(1 if fails else 0)
