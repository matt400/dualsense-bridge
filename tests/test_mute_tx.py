"""Nadajnik: przycisk mute z hidraw (sender_hidraw) i stały przydział pad -> slot (slots.json)."""
import asyncio, json, os, sys, tempfile, types
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
TMP = tempfile.mkdtemp()
C.SLOTS_FILE = os.path.join(TMP, "slots.json")
import sender_ctrl, sender_hidraw as H, sender_state as state, sender_supervisor as sup
from proto import KEY_BIT, MUTE_KEY, T_STATE
from sender_ctrl import Ctrl
out = []
sender_ctrl.net.emit = lambda t, i, *v: out.append((t, i, v))
MUTE = KEY_BIT[MUTE_KEY]

# ---------- raporty HID ----------
usb = bytearray(64); usb[0] = 0x01
bt = bytearray(78); bt[0] = 0x31
assert H.mute_state(usb) is False and H.mute_state(bt) is False
usb[10] |= 0x04; bt[11] |= 0x04
assert H.mute_state(usb) is True and H.mute_state(bt) is True
assert H.mute_state(bytes([0x01] + [0] * 9)) is None, "krótki raport 0x01 po BT (tryb uproszczony) - bez mute"
assert H.mute_state(b"") is None and H.mute_state(bytes([0x05] * 64)) is None
print("OK   raporty: USB 0x01 i BT 0x31, inne / krótkie pominięte")

# ---------- znajdowanie /dev/hidrawN w sysfs ----------
root = os.path.join(TMP, "sys")
hid = os.path.join(root, "devices/virtual/misc/uhid/0005:054C:0CE6.0007")
os.makedirs(os.path.join(hid, "input/input21/event9"))
os.makedirs(os.path.join(hid, "hidraw/hidraw3"))
os.makedirs(os.path.join(root, "class/input"))
os.symlink(os.path.join(hid, "input/input21/event9"), os.path.join(root, "class/input/event9"))
dev = types.SimpleNamespace(path="/dev/input/event9")
assert H.find_hidraw(dev, root) == "/dev/hidraw3", H.find_hidraw(dev, root)
assert H.find_hidraw(types.SimpleNamespace(path="/dev/input/event77"), root) is None
print("OK   hidraw tego samego urządzenia HID co pad evdev")

# ---------- odczyt: zmiana mute -> pakiet stanu z bitem, puszczenie, zamknięcie węzła ----------
async def t_stream():
    c = Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
    r, w = os.pipe()
    os.set_blocking(r, False)
    path = f"/proc/self/fd/{r}"
    task = asyncio.create_task(H.stream_mute(c, path))
    await asyncio.sleep(0.05)
    os.write(w, bytes(usb)); await asyncio.sleep(0.05)
    assert c.mute and c.mask & MUTE and out and out[-1][0] == T_STATE and out[-1][2][8] & MUTE, out[-1:]
    n = len(out)
    os.write(w, bytes(usb)); await asyncio.sleep(0.05)
    assert len([x for x in out[n:] if x[2][8] & MUTE]) <= 2, "powtórzony stan to nie nowe naciśnięcie (najwyżej powtórki burst)"
    usb[10] &= ~0x04
    os.write(w, bytes(usb)); await asyncio.sleep(0.05)
    assert not c.mute and not out[-1][2][8] & MUTE
    usb[10] |= 0x04
    os.write(w, bytes(usb)); await asyncio.sleep(0.05)
    assert c.mute
    os.close(w); await asyncio.wait_for(task, 1)
    assert not c.mute and not c.mask & MUTE, "węzeł zniknął z wciśniętym mute - puszczony"
    c.mask = KEY_BIT[304]; c.mute = True; c.mask |= MUTE
    class Dev:
        def absinfo(self, code): raise OSError
        def active_keys(self): return [304]
    c.pad_dev = Dev(); c.resync()
    assert c.mask == KEY_BIT[304] | MUTE, "resync z evdev nie gubi mute (nie ma go w evdev)"
asyncio.run(t_stream())
print("OK   mute: pakiet przy zmianie, puszczenie przy utracie węzła, resync zachowuje bit")

async def t_no_access():
    c = Ctrl(1, "x", None, None, None)
    await H.stream_mute(c, os.path.join(TMP, "brak-hidraw"))       # brak węzła / uprawnień - kończy się cicho
    assert not c.mute
asyncio.run(t_no_access())
print("OK   brak dostępu do hidraw: pad działa dalej bez mute")

# ---------- stały slot pada ----------
A, B, Cc = "aa:aa:aa:aa:aa:01", "bb:bb:bb:bb:bb:02", "cc:cc:cc:cc:cc:03"
def fresh():
    state.active.clear(); state.slots.clear(); state.saved_slots = None
fresh()
assert sup.pick_slot(B) == 0 and sup.pick_slot(A) == 1
state.active.update({B: 1, A: 1})
assert json.load(open(C.SLOTS_FILE)) == {B: 0, A: 1}
fresh()                                                   # restart nadajnika: A włączony pierwszy
assert sup.pick_slot(A) == 1, "A wraca na swój slot 1, mimo że połączył się pierwszy"
state.active[A] = 1
assert sup.pick_slot(B) == 0
fresh(); state.active[A] = 1; state.slots[A] = 1
assert sup.pick_slot(Cc) == 0, "nowy pad: wolny slot"
assert json.load(open(C.SLOTS_FILE)) == {A: 1, Cc: 0}, "slot przejęty przez nowy pad - stary wpis usunięty"
fresh(); state.active[Cc] = 0; state.slots[Cc] = 0; state.active[A] = 1; state.slots[A] = 1
assert sup.pick_slot(B) is None, "brak wolnych slotów"
fresh()
assert sup.pick_slot("usb-0000:00:14.0-1/input3") == 0
assert "usb-0000:00:14.0-1/input3" not in json.load(open(C.SLOTS_FILE)), "pad bez MAC nie jest zapamiętywany"
open(C.SLOTS_FILE, "w").write("zepsuty"); fresh()
assert sup.pick_slot(A) == 0, "uszkodzony slots.json -> zwykły przydział"
print("OK   slots.json: ten sam pad = ten sam slot po restarcie, nowy pad bierze wolny")
print("PASS mute_tx")
