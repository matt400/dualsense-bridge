"""Receiver: opóźnienie przycisk -> gra (rx_net / rx_ds4 / rx_latency), dziennik zdarzeń (rx_events, pakiet event),
ustawienia dla nadajnika (scfg, build2), lightbar od baterii (rx_lightbar, rx_feedback) i nowe sekcje settings.json."""
import json, os, socket, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (zaślepki Windows, TMP, store, sent)
import rx_ds4
import rx_events as ev
import rx_latency as L
import rx_lightbar as LB
import rx_net
import rx_settings as R
from proto import (BODY, E_PAD_ON, E_WOL, EV_WARN, LAT_NONE, PAD_KEYS, SCFG_EVENTS, SCFG_WOL, T_BUILD2, T_EVENT, T_SCFG,
                   T_STATE2, Codec)
from rx_buttons import TRIG_ON

ok = lambda m: print("OK  ", m)
cod = Codec()
ARR = 5_000_000_000


def st2(mask=0, lt=0, rt=0, lx=128, gyro=0, phone=900):
    """Pakiet state2 z pomiarem (sieć 400 µs, Bluetooth 100 µs)."""
    st = (lx, 128, lt, 128, 128, rt, 0, 0, mask, 80, 0, 0, 0, 8192, gyro, 0, 0)
    return cod.pack(T_STATE2, 0, *st, (ARR // 1000 - 400) & 0xFFFFFFFF, phone, 100)


def feed2(**kw):
    rx_net.handle(T_STATE2, 0, st2(**kw), ARR)


# ---------- przycisk -> gra: tylko przyciski, D-pad, spusty przez próg ----------
S.ignore_until[0] = 0
rx_net.clear_slot(0, forget_id=True)
S.want[0].set()
feed2()
assert S.states[0]["blat"] is None and S.states[0]["lat"] is not None, "pierwszy stan po połączeniu to nie naciśnięcie"
S.states[0]["lat"] = None
feed2(lx=200); feed2(gyro=5000)
assert S.states[0]["blat"] is None and S.states[0]["lat"] is not None, "gałka / gyro: tylko pomiar ogólny"
feed2(lx=200, gyro=5000, mask=BIT[304])
assert S.states[0]["blat"] == (ARR, 900, 100, 400), S.states[0]["blat"]
S.states[0]["blat"] = None
feed2(lx=200, gyro=5000, mask=BIT[304], rt=TRIG_ON - 1)
assert S.states[0]["blat"] is None, "spust poniżej progu to nie naciśnięcie"
feed2(lx=200, gyro=5000, mask=BIT[304], rt=TRIG_ON, phone=700)
assert S.states[0]["blat"] == (ARR, 700, 100, 400)
feed2(lx=200, gyro=5000, mask=0, rt=TRIG_ON, phone=500)
assert S.states[0]["blat"] == (ARR, 700, 100, 400), "czeka najstarsza zmiana przycisku"
ok("handle: pomiar przycisk -> gra tylko dla przycisków / spustów przez próg, najstarsza czekająca zmiana")


class Pad:
    def update_extended_report(self, rep):
        pass


o = rx_ds4.Out(Pad())
arr = time.perf_counter_ns() - 1_000_000
S.states[0]["lat"] = (arr, 900, 100, 400)
S.states[0]["blat"] = (arr, 700, LAT_NONE, 400)
L.clear(0); L.reset_buttons(0)
o.send(0)
assert S.states[0]["lat"] is None and S.states[0]["blat"] is None
b = L.buttons(0)
assert b.n == 1 and b.bt is None and b.phone == 0.7 and b.net == 0.4 and b.pc >= 1.0 and b.max == b.avg == b.p95, b
assert L.summary(0) is None and len(L._samples[0]) == 1, "pomiar ogólny też zapisany"
ok("rx_ds4: raport w ViGEm kończy oba pomiary (ogólny i przycisku)")

L.reset_buttons(0)
t = time.perf_counter_ns()
for k in range(L.BUTTON_KEEP + 20):
    L.add_button(0, (t - 2_000_000, 1000 + k, 0, 0), t - 1_000_000)
b = L.buttons(0)
assert b.n == L.BUTTON_KEEP and b.max == (1000 + L.BUTTON_KEEP + 19 + 1000) / 1000, "ostatnie BUTTON_KEEP naciśnięć"
L.add_button(0, (t, 1, 1, 1), t - 1)
assert L.buttons(0).n == L.BUTTON_KEEP, "ujemny odcinek PC (raport sprzed odbioru) odrzucony"
L.clear(0)
assert L.buttons(0) is not None, "koniec okna 5 s / cisza nie kasuje naciśnięć"
S.alive[0] = True
rx_net.clear_slot(0)
assert L.buttons(0) is not None, "reset stanu po ciszy zostawia naciśnięcia tego samego pada"
rx_net.clear_slot(0, forget_id=True)
assert L.buttons(0) is None, "inny pad w slocie - od zera"
ok("rx_latency.buttons: ostatnie 100 naciśnięć bez limitu czasu, reset przy zmianie pada")

# ---------- dziennik zdarzeń ----------
ev.clear()
e1 = ev.info("a"); e2 = ev.warn("b"); e3 = ev.error("c")
assert [e.seq for e in ev.since(0)] == [e1.seq, e2.seq, e3.seq] and ev.since(e3.seq) == []
assert [e.text for e in ev.since(e1.seq)] == ["b", "c"]
assert [ev.visible(e, "warn") for e in (e1, e2, e3)] == [False, True, True] and ev.visible(e3, "error")
assert "⚠  b" in ev.fmt(e2) and ev.fmt(e1)[2] == ":" and "📱" not in ev.fmt(e1)
ev.clear(); assert ev.since(0) == []
for k in range(ev.KEEP + 10):
    ev.info(str(k))
assert len(ev.since(0)) == ev.KEEP and ev.since(0)[-1].text == str(ev.KEEP + 9)
ev.clear()
ok("rx_events: kolejne numery, poziomy, format linii, limit pamięci, czyszczenie")

R.apply(R.defaults())
data = cod.pack(T_EVENT, 1, 77, 0, E_WOL, 30, b"aa:bb:cc:dd:ee:ff")
p = cod.parse(data)
assert p[0] == T_EVENT
ev.clear()
ev.on_sender_event(1, data, now=1000.0)
e = ev.since(0)[0]
assert e.sender and e.level == "info" and e.t == 970.0 and "aa:bb:cc:dd:ee:ff" in e.text and "pad 2" in e.text.lower(), e
assert ev.on_sender_event(1, data) is None and len(ev.since(0)) == 1, "powtórka UDP odrzucona po numerze"
ev.on_sender_event(255, cod.pack(T_EVENT, 255, 78, EV_WARN, 250, 0, b"?"))
assert ev.since(0)[-1].level == "warn" and "250" in ev.since(0)[-1].text, "nieznany kod - ogólny opis"
assert "📱" in ev.fmt(e)
ok("zdarzenie nadajnika: tekst w języku okna, czas z wieku, powtórka odrzucona")


ev.clear()
assert rx_net.on_event(0, cod.pack(T_EVENT, 0, 5, 0, E_PAD_ON, 0, b"aa")) is None and ev.since(0) == [], "wyłączone"
R.apply(R.Settings(log=R.LogConfig(sender=True)))
assert rx_net.on_event(0, cod.pack(T_EVENT, 0, 6, 0, E_PAD_ON, 0, b"aa")).sender and len(ev.since(0)) == 1
ok("pętla sieci: zdarzenia nadajnika tylko, gdy włączone w panelu")

# ---------- ustawienia dla nadajnika: scfg ----------
R.apply(R.defaults())
S.peer = None; sent.clear()
R.update(None, wol=R.WolConfig(enabled=False))
assert not sent, "nadajnik jeszcze nieznany (start receivera) - bez wysyłki"
R.apply(R.defaults())
S.peer = ("192.168.1.50", 5005)
v = rx_net.scfg_values()
assert v == (SCFG_WOL, bytes(6), bytes(4), PAD_KEYS.index(315), 1000), v
sent.clear()
R.update(None, wol=R.WolConfig(mac="AA-BB-CC-DD-EE-FF".lower().replace("-", ":"), bcast="192.168.1.255", button="triangle",
                               hold_ms=2000), log=R.LogConfig(sender=True))
sc = [x for x in sent if x[0] == T_SCFG]
assert len(sc) == 3 and sc[0][1] == 255, "zmiana od razu, 3 kopie (UDP)"
assert BODY[T_SCFG].unpack_from(sc[0][3], 4) == (SCFG_WOL | SCFG_EVENTS, bytes.fromhex("aabbccddeeff"),
                                                 socket.inet_aton("192.168.1.255"), PAD_KEYS.index(307), 2000)
sent.clear()
R.update(None, mouse=R.MouseConfig(sens=1.0))
assert not [x for x in sent if x[0] == T_SCFG], "inne ustawienia - bez scfg"
R.apply(R.defaults())
ok("scfg: Wake-on-LAN i zdarzenia do nadajnika przy zmianie (3 kopie), wartości w pakiecie")

w = rx_net.SenderWatch()
ev.clear(); sent.clear()
S.peer = ("192.168.1.50", 5005)
S.last_pkt = 100.0
w.tick(100.5)
assert w.online and "192.168.1.50" in ev.since(0)[-1].text and [x for x in sent if x[0] == T_SCFG], "online + scfg"
n = len(sent); w.tick(101.0)
assert len(sent) == n, "scfg co SCFG_S, nie co sprawdzenie"
S.last_pkt = 100.0 + rx_net.SCFG_S
w.tick(100.5 + rx_net.SCFG_S); assert len(sent) == n + 1
S.sender_wol = ("x", "y")
w.tick(S.last_pkt + S.SENDER_TIMEOUT + 1)
assert not w.online and ev.since(0)[-1].level == "warn" and S.sender_wol is None
ok("nadajnik online / offline w dzienniku, scfg przy pierwszym kontakcie i co 5 s")

rx_net.on_build(cod.pack(T_BUILD2, 255, b"abc1234", 5, 0, bytes.fromhex("112233445566"), socket.inet_aton("10.0.0.255")),
                time.monotonic(), T_BUILD2)
assert S.sender_wol == ("11:22:33:44:55:66", "10.0.0.255") and S.sender_build[0] == "abc1234"
rx_net.on_build(cod.pack(T_BUILD2, 255, b"abc1234", 5, 0, bytes(6), bytes(4)), time.monotonic(), T_BUILD2)
assert S.sender_wol == ("", "")
ok("build2: MAC i broadcast, których użyje nadajnik")

# ---------- settings.json: nowe sekcje ----------
path = os.path.join(TMP, "s_extras.json")
s = R.Settings(lightbar=R.LightbarConfig(20, {"*": R.PadLight("battery", "", True),
                                             "aa:bb:cc:dd:ee:ff": R.PadLight("solid", "#ff8800", False)}),
               wol=R.WolConfig(False, "aa:bb:cc:dd:ee:ff", "192.168.1.255", "share", 700),
               log=R.LogConfig("warn", 300, True))
R.save(s, path)
assert R.load(path) == s
raw = json.load(open(path))
raw["lightbar"]["pads"]["zly"] = {"mode": "disco"}
raw["lightbar"]["low_pct"] = 99
raw["wol"].update(mac="nie-mac", button="ps", hold_ms=10)
raw["log"]["level"] = "x"
json.dump(raw, open(path, "w"))
s2 = R.load(path)
assert "zly" not in s2.lightbar.pads and s2.lightbar.pads == s.lightbar.pads and s2.lightbar.low_pct == R.LightbarConfig().low_pct
assert s2.wol.mac == "" and s2.wol.button == "options" and s2.wol.hold_ms == 1000 and s2.log.level == "info"
assert s2.wol.bcast == "192.168.1.255" and s2.log.sender, "dobre pola zostają"
assert R.defaults(0.5, 25).lightbar.low_pct == 25 and R.defaults(0.5, 99).lightbar.low_pct == R.LOW_PCT[1]
assert s.lightbar.get("11:22:33:44:55:66") == R.PadLight("battery", "", True) and R.LightbarConfig().get("x") == R.PadLight()
ok("settings.json: lightbar per pad + domyślny, Wake-on-LAN, dziennik; złe wartości -> domyślne")

# ręczna edycja: lista / obiekt tam, gdzie wybór z listy (unhashable) nie może wywalić startu receivera;
# kolor tylko #rrggbb - "#0x1234" przechodził int(..., 16), a potem wywracał wątek feedbacku (hex_rgb)
raw = json.load(open(path))
raw["log"]["level"] = ["info"]
raw["ui"] = {"lang": {"pl": 1}}
raw["master"] = {"when_absent": ["all"]}
raw["lightbar"]["pads"] = {"*": {"mode": ["solid"]}, "aa:bb:cc:dd:ee:ff": {"color": "#0x1234"},
                           "11:22:33:44:55:66": {"color": "# 12345"}, "22:22:33:44:55:66": {"color": "#12_345"},
                           "33:22:33:44:55:66": {"color": "#A0b1C2"}}
json.dump(raw, open(path, "w"))
s3 = R.load(path)
assert s3.log.level == "info" and s3.ui.lang == "pl" and s3.master.when_absent == "all", s3
assert set(s3.lightbar.pads) == {"33:22:33:44:55:66"} and s3.lightbar.pads["33:22:33:44:55:66"].color == "#a0b1c2", s3.lightbar.pads
assert all(LB.hex_rgb(p.color) for p in s3.lightbar.pads.values())
ok("settings.json: zły typ (lista / obiekt) i zły kolor -> domyślne, bez wyjątku")

# ---------- lightbar ----------
assert LB.battery_rgb(10, 15) == (255, 0, 0) and LB.battery_rgb(15, 15) == (255, 0, 0)
assert LB.battery_rgb(100, 15) == LB.battery_rgb(80, 15) == (0, 220, 60)
mid = LB.battery_rgb(47, 15)
assert mid[0] > 200 and 100 < mid[1] < 200, mid
greens = [LB.battery_rgb(p, 15)[1] for p in range(15, 81)]
assert greens == sorted(greens), "im więcej baterii, tym bardziej zielony"
idle = (0, 0, 64)
assert LB.own_color(R.PadLight(), 50, 15, idle) == (idle, False)
assert LB.own_color(R.PadLight("solid", "#000040", True), 50, 15, idle) == (idle, False), "kolor = domyślny"
assert LB.own_color(R.PadLight("solid", "#ff8800", True), 50, 15, idle) == ((255, 136, 0), True)
assert LB.own_color(R.PadLight("battery"), 90, 15, idle) == ((0, 220, 60), True)
assert LB.own_color(R.PadLight("battery"), 255, 15, idle) == (idle, False), "bateria nieznana - kolor stały"
assert LB.blink_on(10.1) and not LB.blink_on(10.5) and abs(LB.next_edge(10.1) - 0.3) < 1e-9 and abs(LB.next_edge(10.5) - 0.5) < 1e-9
assert LB.blink_rgb((1, 2, 3), 10.1) == LB.LOW_RGB and LB.blink_rgb((1, 2, 3), 10.6) == (1, 2, 3)
assert LB.blink_rgb(LB.LOW_RGB, 10.6) == (0, 0, 0), "czerwona baza - miganie czerwień / zgaszony"
assert LB.rgb_hex((255, 136, 0)) == "#ff8800" and LB.hex_rgb("#ff8800") == (255, 136, 0)
ok("rx_lightbar: kolor wg baterii (czerwony -> żółty -> zielony), stały, domyślny, faza migania")

C.IDLE_RGB = idle
S.alive[0] = True; S.pad_id[0] = "aa:bb:cc:dd:ee:ff"
F.reset(0); F.pulses[0] = None; F.fb_sent[0] = None
S.batt_info[0] = (50, 0)
R.apply(R.defaults(0.5, 15))
assert F._target(0, 10.1) == ((0, 0, *idle), False, False), "domyślnie bez gry nic nie wysyłamy (jak dotąd)"
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {"aa:bb:cc:dd:ee:ff": R.PadLight("battery", "", True)})))
val, ours, blink = F._target(0, 10.1)
assert ours and not blink and val[2:] == LB.battery_rgb(50, 15), val
F.fb[0].update(set=True, rgb=True, small=1, large=2, r=9, g=9, b=9)
S.game = "steam.exe"
assert F._target(0, 10.1)[0][2:] == LB.battery_rgb(50, 15), "kolor z PC poza grą (Steam) - kolor wg baterii"
S.game = "game.exe"
assert F._target(0, 10.1)[0] == (1, 2, 9, 9, 9), "gra ma pierwszeństwo przed kolorem baterii"
S.batt_info[0] = (10, 0)
assert F._target(0, 10.1)[0] == (1, 2, 255, 0, 0) and F._target(0, 10.1)[2], "niska bateria miga nad kolorem gry"
assert F._target(0, 10.6)[0] == (1, 2, 9, 9, 9), "druga faza migania: kolor gry"
S.batt_info[0] = (10, 1)
assert F._target(0, 10.1)[0] == (1, 2, 9, 9, 9) and not F._target(0, 10.1)[2], "ładuje się - bez migania"
S.batt_info[0] = (10, 0)
F.pulses[0] = (time.monotonic() + 5, 0, 200, (255, 200, 0))
assert F._target(0, 10.1)[0] == (0, 200, 255, 200, 0), "złoty błysk mastera nad miganiem"
F.pulses[0] = None
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {"aa:bb:cc:dd:ee:ff": R.PadLight("battery", "", False)})))
assert F._target(0, 10.1)[0] == (1, 2, 9, 9, 9), "miganie wyłączone dla tego pada"
F.fb[0].update(set=False)
S.pad_id[0] = "11:11:11:11:11:11"
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {"*": R.PadLight("solid", "#ff8800", True)})))
assert F._target(0, 10.6) == ((0, 0, 255, 136, 0), True, True), "pad bez wpisu - ustawienie domyślne"
ok("rx_feedback: gra ma pierwszeństwo, kolor wg baterii / stały, miganie nad grą (nie przy ładowaniu), potwierdzenia na wierzchu")

fsent = []
S.batt_info[0] = (60, 0); S.alive[1] = False
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {"*": R.PadLight("battery", "", True)})))
import threading
S.stop.clear()
th = threading.Thread(target=F.feedback_thread, args=(lambda i, v: fsent.append((time.monotonic(), i, v)),), daemon=True)
th.start(); time.sleep(0.1)
assert fsent and fsent[-1][2][2:] == LB.battery_rgb(60, 15), fsent
S.batt_info[0] = (10, 0); F.fb_event.set(); time.sleep(2.3)
reds = [x for x in fsent if x[2][2:] == LB.LOW_RGB]
offs = [x for x in fsent if x[2][2:] == (0, 0, 0)]
assert len(reds) >= 2 and len(offs) >= 1, ("miganie: pętla budzi się na zmianę fazy", [(round(t, 2), v) for t, _i, v in fsent])
S.stop.set(); th.join(2)
ok("pętla feedbacku: kolor baterii od razu, miganie w rytmie BLINK_PERIOD_S")
R.apply(R.defaults())
import rx_master
from dataclasses import replace
ev.clear(); S.pad_id[0] = "aa:bb:cc:dd:ee:ff"
R.apply(replace(R.cur, master=replace(R.cur.master, pad="aa:bb:cc:dd:ee:ff")))
R.apply(replace(R.cur, master=replace(R.cur.master, pad="")))
assert [e.text for e in ev.since(0)] == ["Master: Pad 1 (aa:bb:cc:dd:ee:ff)", "Master: brak"], [e.text for e in ev.since(0)]
ok("zmiana mastera w dzienniku (korona, PS + R3, Ustawienia - jedno miejsce)")
print("EXTRAS OK")
