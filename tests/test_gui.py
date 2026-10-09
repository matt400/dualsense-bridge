import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                              # noqa
import tkinter as tk
from proto import T_CMD
C.cfg["tray"] = False; C.cfg["gui_start_visible"] = True
result = {}

def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)

class TestTk(tk.Tk):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.after(800, self.step1)
    def step1(self):
        S.alive[0] = True; S.batt_info[0] = (64, 0)
        S.info[0] = (1.2, 540, 500, time.monotonic()); S.link_loss = 0.0; S.pps[0] = 250.0
        store.save(S.profile_key(0) + "@doom.exe", {}); S.set_game("doom.exe")
        self.after(900, self.step2)
    def step2(self):
        texts = [w.cget("text") for w in walk(self) if isinstance(w, tk.Label)]
        result["link"] = [t for t in texts if "ping" in t]
        result["info"] = [t for t in texts if "profile:" in t]
        off = [w for w in walk(self) if isinstance(w, tk.Button) and w.cget("text") == "Off"]
        result["off_state"] = [str(b.cget("state")) for b in off]
        sent.clear(); off[0].invoke()
        result["cmds"] = sum(1 for x in sent if x[0] == T_CMD)
        self.update()
        import subprocess
        subprocess.run(["import", "-window", "root", os.path.join(os.path.dirname(__file__), "gui.png")])
        self.after(100, self.destroy)

tk.Tk = TestTk
import rx_gui
rx_gui.ask = lambda *a: True
rx_gui.run_gui()
print(result)
assert result["link"] and "ping 1.2 ms" in result["link"][0] and "pad 540 Hz" in result["link"][0] and "loss 0.0%" in result["link"][0]
assert "profile: doom.exe" in result["info"][0], result["info"]
assert result["off_state"] == ["normal", "disabled"] and result["cmds"] == 3
print("OK   GUI: jakość łącza, profil gry, przycisk Off (aktywny tylko dla podłączonego pada)")
