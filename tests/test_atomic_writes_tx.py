"""Nadajnik: regresje z dziesiątego przeglądu - zapis plików (sloty, kalibracja, Wake-on-LAN) atomowy z fsync,
wspólne zadania w tle i wyszukiwanie pada po slocie, efekt "Off" spustu bez zmian.
    python3 tests/test_atomic_writes_tx.py sender"""
import asyncio, json, os, sys, tempfile, types
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import proto
import sender_calib as calib
import sender_rumble as R
import sender_state as state
import sender_supervisor as sup
import sender_wol

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

TMP = tempfile.mkdtemp()

# ---------- 1. pliki nadajnika przez wspólny zapis atomowy (fsync) ----------
synced = []
real_fsync = os.fsync
os.fsync = lambda fd: synced.append(fd) or real_fsync(fd)
C.SLOTS_FILE = os.path.join(TMP, "slots.json")
state.saved_slots = {"aa:bb:cc:dd:ee:01": 1}
sup._save_slots()
check(json.load(open(C.SLOTS_FILE)) == {"aa:bb:cc:dd:ee:01": 1} and len(synced) == 1, "slots.json zapisany z fsync")
C.GYRO_CAL_FILE = os.path.join(TMP, "gyro.json")
check(calib.save("AA:BB:CC:DD:EE:01", [1.04, 2, 3]) and json.load(open(C.GYRO_CAL_FILE))["aa:bb:cc:dd:ee:01"]["bias"] == [1.0, 2, 3],
      "kalibracja gyro zapisana")
wol = os.path.join(TMP, "wol.json")
sender_wol.save(wol)
check("learned_mac" in json.load(open(wol)) and len(synced) == 3, "wol.json zapisany z fsync")
os.fsync = real_fsync
check(not [n for n in os.listdir(TMP) if n.endswith(".tmp")], "bez plików .tmp")

real_replace = os.replace
def boom(*_a):
    raise OSError("tylko do odczytu")
os.replace = boom
state.saved_slots = {"aa:bb:cc:dd:ee:02": 0}
sup._save_slots()                  # błąd tylko w logu - nadajnik działa dalej
os.replace = real_replace
check(json.load(open(C.SLOTS_FILE)) == {"aa:bb:cc:dd:ee:01": 1} and not os.path.exists(C.SLOTS_FILE + ".tmp"),
      "nieudany zapis: stary plik zostaje, .tmp usunięty")

# ---------- 2. wspólne state.spawn / state.ctrl_by_idx ----------
a, b = types.SimpleNamespace(idx=0, fb=None), types.SimpleNamespace(idx=1, fb=None)
for c in (a, b):
    c.apply_feedback = lambda *v, c=c: setattr(c, "fb", v)
    c.set_triggers = lambda v, c=c: setattr(c, "trig", v)
state.active.clear(); state.active.update({"a": a, "b": b})
check(state.ctrl_by_idx(1) is b and state.ctrl_by_idx(3) is None, "ctrl_by_idx")
sup.dispatch_feedback(1, (1, 2, 3, 4, 5)); sup.dispatch_triggers(0, (1, 2, 3, 4)); sup.dispatch_feedback(7, (0,) * 5)
check(b.fb == (1, 2, 3, 4, 5) and a.fb is None and a.trig == (1, 2, 3, 4), "feedback i spusty trafiają do pada w slocie")
state.active.clear()

async def main():
    done = []
    async def job():
        done.append(1)
    t = state.spawn(job())
    check(t in state.bg_tasks, "zadanie w tle trzymane w bg_tasks")
    await t
    await asyncio.sleep(0)
    check(done == [1] and t not in state.bg_tasks, "po zakończeniu znika z bg_tasks")
asyncio.run(main())

# ---------- 3. efekt Off spustu: te same bajty co wcześniej ----------
check(R.OFF_EFFECT == bytes([R.TRIG_OFF]) + bytes(10) and len(R.OFF_EFFECT) == R.TRIGGER_LEN, "efekt Off: 0x05 + 10 zer")
check(R.trigger_effect(0, 0) == R.weapon_effect(9, 3) == R.vibration_effect(0, 0, 9) == R.OFF_EFFECT, "złe parametry -> Off")
check(R.trigger_effect(3, 6)[0] == R.TRIG_FEEDBACK and R.weapon_effect(5, 6)[0] == R.TRIG_WEAPON, "efekty jak dotąd")
check(proto.write_file is not None, "proto.write_file")

if fails:
    print("FAIL", len(fails), "błędów")
    sys.exit(1)
print("WSZYSTKO OK")
