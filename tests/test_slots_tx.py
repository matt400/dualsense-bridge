"""Nadajnik: stały przydział pad -> slot (slots.json)."""
import json, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
TMP = tempfile.mkdtemp()
C.SLOTS_FILE = os.path.join(TMP, "slots.json")
import sender_state as state, sender_supervisor as sup

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
assert json.load(open(C.SLOTS_FILE)) == {B: 0, A: 1}, "slot pożyczony od innego pada nie jest zapisywany - właściciel go zachowuje"
fresh(); state.active[Cc] = 0; state.slots[Cc] = 0; state.active[A] = 1; state.slots[A] = 1
assert sup.pick_slot(B) is None, "brak wolnych slotów"
fresh()
assert sup.pick_slot("usb-0000:00:14.0-1/input3") == 0
assert "usb-0000:00:14.0-1/input3" not in json.load(open(C.SLOTS_FILE)), "pad bez MAC nie jest zapamiętywany"
open(C.SLOTS_FILE, "w").write("zepsuty"); fresh()
assert sup.pick_slot(A) == 0, "uszkodzony slots.json -> zwykły przydział"
print("OK   slots.json: ten sam pad = ten sam slot po restarcie, nowy pad bierze wolny")
print("PASS slots_tx")
