"""Receiver: efekty spustów ustawione przez grę na wirtualnym DualSense (HIDMaestro) - trig3 tylko do nadajnika v11+,
profil wygrywa (rozstrzyga nadajnik), gra steruje spustami = bez wibracji z gry na spustach, funkcja mastera "triggers",
zostają przy zmianie programu, zwolnione po zamknięciu programu, który je ustawił, i po odłączeniu pada.
    python3 tests/test_gametrig.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (zaślepki Windows, TMP, store, sent)
import rx_events as ev
import rx_games
import rx_settings
from dataclasses import replace
from proto import (BODY, CAP_TRIG_FX, CAP_TRIG_RAW, RFX_ON, T_TRIG2, T_TRIG3, TFX_OFF, TFX_RES, TRG3_L2, TRG3_R2,
                   TRIG2_OFF, Codec)
from rx_buttons import normalize_profile

ok = lambda m: print("OK  ", m)
G_L2 = bytes([0x25, 0x30, 0x00, 0x05] + [0] * 7)
G_R2 = bytes([0x21, 0xFF, 0x03] + [0] * 8)
OFF = bytes([0x05] + [0] * 10)
Z = bytes(11)

A = "aa:aa:aa:aa:aa:07"
S.pad_id[0] = A
S.alive[0] = True
store.save(A, normalize_profile({"triggers": {"r2": {"mode": "res", "start": 2, "force": 5},
                                              "rumble": {"on": True, "gain": 80, "freq": 40}}}))
S.reload_profile(0)
prof = (TFX_OFF, 0, 0, 0, TFX_RES, 2, 5, 0, RFX_ON, 80, 40, 0)
S.sender_info[0] = (11, 3 | CAP_TRIG_FX | CAP_TRIG_RAW, 0)
assert F.trigger_target(0) == prof, "gra nic nie ustawiła - zwykły trig2"

S.game = "forza.exe"
F.game(0, trig=(G_L2, None))
assert F.fb[0]["trig"] == (G_L2, None) and F.fb[0]["trig_owner"] == "forza.exe"
v = F.trigger_target(0)
assert v == (*prof[:8], 0, 0, 0, 0, G_L2, Z, TRG3_L2), ("efekt gry, wibracja z gry na spustach wyłączona", v)
F.game(0, trig=(None, G_R2))
assert F.fb[0]["trig"] == (G_L2, G_R2), "spust bez flagi w raporcie zostaje"
v = F.trigger_target(0)
assert v[12:] == (G_L2, G_R2, TRG3_L2 | TRG3_R2) and v[4:8] == prof[4:8], "R2 profilu jedzie dalej - nadajnik da mu pierwszeństwo"
F.game(0, trig=(OFF, OFF))
assert F.trigger_target(0)[8:12] == prof[8:12], "gra zwolniła spusty (Off) - wibracja z gry na spustach wraca"
ok("trig3: profil + surowe efekty gry, flagi; gra sterująca spustami wyłącza wibrację z gry na spustach")

c = Codec()
F.game(0, trig=(G_L2, G_R2))
v = F.trigger_target(0)
pkt = c.pack(T_TRIG3, 0, *v)
assert c.parse(pkt)[0] == T_TRIG3 and BODY[T_TRIG3].unpack_from(pkt, 4) == v
sent.clear()
N.send_triggers(0, v, 1)
assert sent[-1][0] == T_TRIG3, sent[-1]
ok("rx_net.send_triggers: 15 wartości -> trig3")

S.sender_info[0] = (10, 3 | CAP_TRIG_FX, 0)
assert F.trigger_target(0) == prof, "nadajnik v10 - tylko profil (trig2, starszy odrzuciłby długość)"
S.sender_info[0] = (11, 3 | CAP_TRIG_FX | CAP_TRIG_RAW, 0)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="bb:bb:bb:bb:bb:bb",
                                                          only_master=("triggers",), when_absent="nobody")))
assert F.trigger_target(0) == TRIG2_OFF, "efekty tylko dla mastera - gra też nie ustawi spustów innym padom"
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="", only_master=())))
ok("nadajnik bez CAP_TRIG_RAW - trig2; funkcja mastera obejmuje efekty gry")

calls = []
F.triggers_tick(0, 100.0, lambda i, v, n: calls.append((v, n)))
assert calls and len(calls[-1][0]) == 15 and calls[-1][1] == F.TRIG_REPEAT
F.triggers_tick(0, 100.0 + F.TRIG_REFRESH_S, lambda i, v, n: calls.append((v, n)))
assert len(calls) == 2 and calls[-1][1] == 1, "efekty gry odświeżane jak profil (strażnik nadajnika)"
got = []
F.release_triggers(lambda i, v, n: got.append(v))
assert got == [TRIG2_OFF], "zamykanie receivera: trig2 bez efektów zwalnia też efekty gry"
ok("odświeżanie i zwolnienie przy zamykaniu")

seq = ev.since(0)[-1].seq if ev.since(0) else 0
F.fb[0]["trig_note"] = True
F.fb[0]["trig_owner"] = "forza.exe"
F._note_game_triggers(0)
assert any("forza.exe" in e.text and "efekty spustów" in e.text for e in ev.since(seq))
ok("pierwsze efekty gry w dzienniku")

# ---------- zostają przy zmianie programu, znikają z zamknięciem programu ----------
FG[0] = "explorer.exe"
rx_games.poll_once()
assert S.game == "explorer.exe" and F.fb[0]["trig"] == (G_L2, G_R2), "alt-tab: efekty zostają"
W.running_exes = lambda: {"forza.exe", "explorer.exe"}
rx_games._procs[:] = [0.0, None]
rx_games.release_closed(now=1000.0)
assert F.fb[0]["trig"] == (G_L2, G_R2), "gra działa w tle - efekty zostają"
W.running_exes = lambda: {"explorer.exe"}
rx_games.release_closed(now=1001.0)
assert F.fb[0]["trig"] == (G_L2, G_R2), "zrzut procesów najwyżej co PROCS_S"
rx_games.release_closed(now=1000.0 + rx_games.PROCS_S)
assert F.fb[0]["trig"] == F.NO_GAME_TRIG and F.trigger_target(0) == prof, "gra zamknięta - spusty zwolnione"
S.game = "steam.exe"
F.game(0, trig=(G_L2, None))
def boom():
    raise OSError("no snapshot")
W.running_exes = boom
rx_games._procs[:] = [0.0, None]
S.game = "explorer.exe"
rx_games.release_closed(now=2000.0)
assert F.fb[0]["trig"] == (G_L2, None), "nie wiadomo, co działa - efekty zostają"
F.reset(0)
assert F.fb[0]["trig"] == F.NO_GAME_TRIG and F.fb[0]["trig_owner"] is None, "nowy wirtualny pad - bez efektów gry"
ok("zmiana programu nie zwalnia efektów; zamknięcie programu i odłączenie pada - tak")
print("GAME TRIGGERS OK")
