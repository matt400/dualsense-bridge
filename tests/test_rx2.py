import sys, os, time, json, threading
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                              # noqa
import rx_shape as SH
from proto import T_STATE, T_INFO, T_CLICK, T_IDENT, T_CMD, CMD_POWER_OFF, RTT_NONE

ok = lambda msg: print("OK  ", msg)

# ---------- 1. kształtowanie ----------
assert SH.shape_stick(200, 50, {}) == (200, 50)
t = SH.normalize_tuning({"left": {"dz": 0.1}})["left"]
assert SH.shape_stick(128 + 10, 128, t) == (128, 128)                 # w strefie martwej
assert SH.shape_stick(255, 128, t) == (255, 128) and SH.shape_stick(0, 128, t) == (0, 128)
x, _ = SH.shape_stick(128 + 64, 128, t)                               # 50% -> (0.5-0.1)/0.9 = 44%
assert abs(SH._unit(x) - 0.444) < 0.01, x
tc = SH.normalize_tuning({"right": {"curve": 2.0}})["right"]
assert abs(SH._unit(SH.shape_stick(128 + 64, 128, tc)[0]) - 0.25) < 0.01
ta = SH.normalize_tuning({"right": {"anti": 0.2}})["right"]
assert abs(SH._unit(SH.shape_stick(129, 128, ta)[0]) - 0.2) < 0.02     # minimalne wychylenie -> 20%
to = SH.normalize_tuning({"left": {"outer": 0.8}})["left"]
assert SH.shape_stick(128 + 104, 128, to)[0] == 255                    # 82% -> pełne
assert SH.normalize_tuning({"left": {"dz": 0.5, "outer": 0.5}})["left"]["outer"] == 0.55
tt = SH.normalize_tuning({"l2": {"dz": 50, "max": 200}})["l2"]
assert [SH.shape_trigger(v, tt) for v in (0, 40, 50, 125, 200, 255)] == [0, 0, 0, 128, 255, 255]
assert SH.normalize_tuning({"left": {"dz": 0}, "l2": {"dz": 0, "max": 255}, "x": 1}) == {}
assert SH.normalize_tuning({"left": {"dz": "abc", "curve": float("nan")}}) == {}
g = SH.normalize_gyro({"mode": "stick", "hold": "r1", "sens": 99}, ("always", "r1"))
assert g["sens"] == 5.0 and g["hold"] == "r1"
assert SH.normalize_gyro({"mode": "bogus"}, ("always",)) == {}
assert SH.normalize_gyro({"mode": "mouse", "hold": "nope"}, ("always",))["hold"] == "always"
ok("kształtowanie gałek / spustów, normalizacja gyro")

# ---------- 2. profile i magazyn ----------
p = rx_buttons.normalize_profile({"cross": "circle"})                 # stary płaski format
assert p["buttons"] == {"cross": "circle"} and p["tuning"] == {} and p["gyro"] == {}
p = rx_buttons.normalize_profile({"buttons": {}, "tuning": {"left": {"dz": 0.1}}, "gyro": {"mode": "mouse"}})
assert rx_buttons.profile_size(p) == 2
base = S.profile_key(0)
store.save(base, {"buttons": {"cross": "circle"}})
store.save(base + "@game.exe", {})                                     # pusty profil gry też istnieje
assert store.keys_for(base) == [base, base + "@game.exe"] and store.in_file(base + "@game.exe")
data = json.load(open(C.REMAP_PATH))
assert base + "@game.exe" in data["profiles"] and data["version"] == 3
store2 = rx_buttons.RemapStore(C.REMAP_PATH)
assert store2.keys_for(base) == [base, base + "@game.exe"]
store.delete(base + "@game.exe"); assert store.keys_for(base) == [base]
ok("profile: stary format, tuning+gyro, profile gier (zapis, odczyt, usuwanie)")

# ---------- 3. wybór profilu wg gry i ręcznie ----------
store.save(base + "@elden.exe", {"buttons": {"square": "triangle"}})
S.reload_profile(0)
assert S.active[0] == base
assert S.set_game("elden.exe") == [0] and S.active[0] == base + "@elden.exe"
assert S.remap_maps[0]["buttons"] == {"square": "triangle"}
assert S.set_game("explorer.exe") == [0] and S.active[0] == base
S.set_game("elden.exe")
assert S.cycle_profile(0, 1) == base and S.cycle_profile(0, 1) == base + "@elden.exe" and S.cycle_profile(0, -1) == base
assert S.set_game("other.exe") == [] and S.override[0] is None       # zmiana gry kasuje ręczny wybór
S.set_game(None)
ok("profil wg gry na pierwszym planie, przełączanie ręczne")

# ---------- 4. gry: obserwator okna ----------
import rx_games
FG[0] = "elden.exe"; rx_games.poll_once()
assert S.active[0] == base + "@elden.exe" and rx_games.recent == ["elden.exe"]
FG[0] = "msedge.exe"; rx_games.poll_once()
assert S.active[0] == base and rx_games.recent == ["elden.exe"]          # przeglądarka nie jest "grą"
FG[0] = None; rx_games.poll_once(); S.set_game(None)
ok("obserwator okna: profil gry, lista ostatnich gier bez przeglądarek")

# ---------- 5. skróty PS ----------
store.delete(base + "@elden.exe"); S.reload_profile(0)
PS, SHARE = BIT[316], BIT[314]
calls.clear()
state(0)                         # pad już połączony (PS w pierwszym stanie po połączeniu to włączanie pada, nie skrót)
state(0, mask=PS)
assert not S.states[0]["mask"] & PS, "PS nie może iść do gry od razu"
state(0, mask=0)
assert S.states[0]["mask"] & PS, "krótkie PS -> impuls PS dla gry"
time.sleep(0.15)
assert not S.states[0]["mask"] & PS, "impuls PS musi się skończyć"
# PS + Share = zrzut, Share nie trafia do gry
state(0, mask=PS); state(0, mask=PS | SHARE); time.sleep(0.05)
assert ("k", 0x5B, 0x2C) in calls and not S.states[0]["mask"] & SHARE
state(0, mask=SHARE); assert not S.states[0]["mask"] & (SHARE | PS), "share wciąż trzymany po skrócie - stłumiony"
state(0, mask=0); time.sleep(0.12)
assert not S.states[0]["mask"] & PS, "po skrócie bez impulsu PS"
# PS + d-pad: czułość i profil
s0 = M.sens[0]
state(0, mask=PS); state(0, mask=PS, hat=(0, -1)); time.sleep(0.05)
assert M.sens[0] > s0 and S.states[0]["hy"] == 0, "d-pad w skrócie nie idzie do gry"
state(0, mask=PS, hat=(0, 0)); state(0, mask=PS, hat=(0, 1)); time.sleep(0.05)
assert abs(M.sens[0] - s0) < 1e-3
store.save(base + "@g2.exe", {}); state(0, mask=PS, hat=(0, 0)); state(0, mask=PS, hat=(1, 0)); time.sleep(0.05)
assert S.active[0] == base + "@g2.exe"
state(0, mask=0); state(0, hat=(1, 0)); assert S.states[0]["hx"] == 1, "d-pad po puszczeniu działa normalnie"
state(0)
# długie PS = Game Bar
calls.clear(); state(0, mask=PS); time.sleep(C.PS_HOLD_S + 0.15); state(0, mask=0); time.sleep(0.12)
assert ("k", 0x5B, 0x47) in calls and not any(S.states[0]["mask"] & PS for _ in [0])
# PS + klik touchpada: zmiana trybu, bez impulsu PS
mm = S.mouse_mode[0]
state(0, mask=PS); feed(T_CLICK, 0, 1); feed(T_CLICK, 0, 0); state(0, mask=0); time.sleep(0.02)
assert S.mouse_mode[0] != mm and not S.states[0]["mask"] & PS
# ps_modifier wyłączony: PS od razu
C.PS_MODIFIER = False; state(0, mask=PS); assert S.states[0]["mask"] & PS; state(0); C.PS_MODIFIER = True
ok("skróty PS: impuls, zrzut, czułość, profil, Game Bar, przełączanie trybu, wyłączanie modyfikatora")

# ---------- 6. gyro ----------
store.save(base, {"gyro": {"mode": "stick", "hold": "r1", "sens": 1.0}}); S.override[0] = None; S.reload_profile(0)
state(0, gyro=(0, -1024 * 75, 0))                                      # 75°/s yaw, bez R1
assert S.states[0]["rx"] == 128 and not S.states[0]["gyro_on"]
state(0, mask=BIT[311], gyro=(0, -1024 * 75, 0))                       # z R1: połowa wychylenia w prawo
assert 185 < S.states[0]["rx"] < 196 and S.states[0]["gyro_on"], S.states[0]["rx"]
state(0, mask=BIT[311], gyro=(0, -1024 * 75, 0), sticks=(128, 128, 255, 128))
assert S.states[0]["rx"] == 255, "suma z gałką ograniczona do koła"
store.save(base, {"gyro": {"mode": "mouse", "sens": 1.0}}); S.reload_profile(0)
import rx_gyro
calls.clear()
th = threading.Thread(target=rx_gyro.mouse_thread, daemon=True); th.start()
state(0, gyro=(0, -1024 * 100, 0))                                     # 100°/s -> 1000 px/s
time.sleep(0.3); state(0); time.sleep(0.05)
moved = sum(c[2] for c in calls if c[0] == "m" and c[1] == 1)
assert 230 < moved < 330, moved                                       # ~0.3 s * 1000 px/s
n = len(calls); time.sleep(0.1); assert len(calls) == n, "bez obrotu kursor stoi"
ok(f"gyro: gałka z aktywacją R1, ograniczenie do koła, mysz ({moved} px w 0.3 s)")
store.save(base, {}); S.reload_profile(0)

# ---------- 7. info, statystyki łącza, polecenia ----------
feed(T_INFO, 0, 12, 540, 538)
assert S.info[0][:3] == (1.2, 540, 538)
feed(T_INFO, 0, RTT_NONE, 0, 0); assert S.info[0][0] is None
ls = N.LinkStats(2)
for sq in [10, 11, 12, 14, 15, 15, 13, 20]:     # zgubione: 16..19 (4), 13 spóźniony
    ls.packet(sq)
ls.states[0] = 5; ls.t0 -= 1.0; ls.tick(time.monotonic())
assert abs(S.link_loss - 100 * (1 - 5 / 10)) < 1e-6 and S.pps[0] > 4, (S.link_loss, S.pps)
sent.clear(); N.send_cmd(0, CMD_POWER_OFF)
assert len(sent) == 3 and all(x[0] == T_CMD and x[1] == 0 for x in sent)
ok("pakiet info, utrata pakietów z numerów sekwencyjnych, polecenie wyłączenia")

# ---------- 8. potwierdzenia na padzie ----------
fsent = []
S.alive[0] = True
th = threading.Thread(target=F.feedback_thread, args=(lambda i, v: fsent.append((time.monotonic(), i, v)),), daemon=True)
th.start(); time.sleep(0.05)
assert not fsent, "bez gry i potwierdzeń nic nie wysyłamy"
F.pulse(0, "mouse"); time.sleep(0.35)
vals = [v for _, _, v in fsent]
assert vals[0] == (0, 150, 0, 90, 255) and vals[-1] == (0, 0, *C.IDLE_RGB), vals
F.fb[0].update(set=True, rgb=True, small=10, large=20, r=1, g=2, b=3); F.fb_event.set(); time.sleep(0.05)
F.pulse(0, "up"); time.sleep(0.2)
vals = [v for _, _, v in fsent]
assert (160, 0, 1, 2, 3) in vals and vals[-1] == (10, 20, 1, 2, 3), vals[-3:]
ok("potwierdzenia: impuls nałożony na kolor gry, powrót do koloru gry / bezczynnego")

# ---------- 9. raport DS4 i DSU nadal działają ----------
import rx_report, rx_dsu
state(0, mask=BIT[304]); rep = rx_report.build_report(S.states[0], 1, False); assert len(rep) == 63 and rep[4] & 0x20
pkt = rx_dsu.dsu_data(0, S.states[0], 1); assert len(pkt) == 100 and pkt[37] == 0x40
ok("raport DS4 / DSU")
print("RECEIVER v5 OK")
