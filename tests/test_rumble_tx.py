"""Nadajnik: wibracja (sender_rumble) - evdev FF albo raport HID przez hidraw, strażnik, przełączenie po błędzie."""
import asyncio, os, sys, tempfile, types, zlib
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import sender_rumble as R
from evdev import ecodes as ec
from proto import CAP_TRIG_FX, CAP_TRIG_RAW, CAP_TRIGGERS, RUMBLE_EVDEV, RUMBLE_HIDRAW

TMP = tempfile.mkdtemp()
ok = lambda m: print("OK  ", m)

# ---------- raport wyjściowy ----------
bt = R.output_report(True, 3, 40, 200)
usb = R.output_report(False, 0, 40, 200)
assert len(bt) == 78 and len(usb) == 63, (len(bt), len(usb))
assert bt[0] == 0x31 and bt[1] == 0x30 and bt[2] == 0x10, bt[:3].hex()
c = bt[3:]
assert c[0] == 0x03 and c[2] == 40 and c[3] == 200 and c[38] == 0x04, "flagi wibracji, silnik prawy (słaby) / lewy (mocny)"
v1, v2 = R.output_report(False, 0, 1, 1, False), R.output_report(False, 0, 1, 1, True)
assert v1[1] == 0x03 and v1[39] == 0 and v2[1] == 0x02 and v2[39] == 0x04, "v1: flaga zgodności w flag0, v2: w flag2 (jak jądro)"
assert c[1] == 0 and not any(c[41:47]), "bez flag i wartości lightbara / diod - nimi steruje sterownik"
assert int.from_bytes(bt[-4:], "little") == zlib.crc32(b"\xa2" + bt[:-4]) & 0xFFFFFFFF, "CRC32 z ziarnem 0xA2"
assert usb[0] == 0x02 and usb[1:48] == c[:47]
assert R.output_report(True, 0x13, 0, 0)[1] == 0x30, "numer sekwencji 4 bity"
ok("raport: USB 63 B / BT 78 B, tylko flagi wibracji, CRC32")
lb = R.output_report(True, 1, 200, 255, None, (255, 128, 0))[3:]
assert lb[1] == 0x04 and lb[44:47] == bytes([255, 128, 0]) and lb[2:4] == bytes([200, 255]) and not any(lb[41:44])
ok("raport z kolorem lightbara: flaga + RGB w tym samym raporcie co wibracja")

# ---------- sysfs: hidraw i rodzaj połączenia ----------
root = os.path.join(TMP, "sys")
for bus, ev, raw in (("0005", "event9", "hidraw3"), ("0003", "event4", "hidraw1")):
    hid = os.path.join(root, f"devices/x/{bus}:054C:0CE6.000{ev[-1]}")
    os.makedirs(os.path.join(hid, f"input/input2{ev[-1]}/{ev}"))
    os.makedirs(os.path.join(hid, "hidraw", raw))
    os.makedirs(os.path.join(root, "class/input"), exist_ok=True)
    os.symlink(os.path.join(hid, f"input/input2{ev[-1]}/{ev}"), os.path.join(root, "class/input", ev))
assert R.find_hidraw(types.SimpleNamespace(path="/dev/input/event9"), root) == ("/dev/hidraw3", True)
assert R.find_hidraw(types.SimpleNamespace(path="/dev/input/event4"), root) == ("/dev/hidraw1", False)
assert R.find_hidraw(types.SimpleNamespace(path="/dev/input/event7"), root) == (None, False)
raw5 = os.path.join(root, "class/hidraw/hidraw5/device")
os.makedirs(raw5)
open(os.path.join(raw5, "uevent"), "w").write("HID_ID=0005:0000054C:00000CE6\nHID_NAME=DualSense Wireless Controller\nHID_UNIQ=AA:BB:CC:00:11:22\n")
assert R.find_hidraw(types.SimpleNamespace(path="/dev/input/event7", uniq="aa:bb:cc:00:11:22"), root) == ("/dev/hidraw5", True), \
    "nietypowa ścieżka w sysfs: hidraw po adresie MAC pada"
assert R.find_hidraw(types.SimpleNamespace(path="/dev/input/event7", uniq="ff:ff:ff:ff:ff:ff"), root) == (None, False)
assert R.kernel_has_hidraw(root) and not R.kernel_has_hidraw(os.path.join(TMP, "nie-ma"))
os.makedirs(os.path.join(root, "class/hidraw/hidraw97"))
open(os.path.join(root, "class/hidraw/hidraw97/dev"), "w").write("240:5\n")
made = []
R.os.mknod, mknod = (lambda p, mode, dev: made.append((p, os.major(dev), os.minor(dev)))), R.os.mknod
R.ensure_node("/dev/hidraw97", root)
R.os.mknod = mknod
assert made == [("/dev/hidraw97", 240, 5)], made
ok("hidraw pada i Bluetooth / USB z sysfs, zapas po MAC, brak węzła /dev -> mknod")


# ---------- evdev FF ----------
class FFDev:
    def __init__(self, ff=True, fail=False):
        self.ff, self.fail, self.log = ff, fail, []
    def capabilities(self):
        return {ec.EV_FF: [ec.FF_RUMBLE]} if self.ff else {ec.EV_KEY: [304]}
    def upload_effect(self, eff):
        if self.fail:
            raise OSError(38, "Function not implemented")
        self.log.append(("upload", eff.u.ff_rumble_effect.strong_magnitude, eff.u.ff_rumble_effect.weak_magnitude))
        return 7
    def write(self, t, code, v):
        self.log.append(("play", code, v))
    def erase_effect(self, i):
        self.log.append(("erase", i))


def frames(path):
    data = open(path, "rb").read()
    return [data[k:k + 78] for k in range(0, len(data), 78)]


async def main():
    d = FFDev()
    r = R.Rumble(0, d, "auto")
    assert r.mode == "evdev"
    r.set(10, 100)
    assert d.log[:2] == [("upload", 100 * 257, 10 * 257), ("play", 7, 1)], d.log
    n = len(d.log); r.set(10, 100)
    assert len(d.log) == n, "bez zmian i świeże - nic nie wysyła"
    r.set(0, 0)
    assert d.log[-1] == ("play", 7, 0)
    r.close()
    assert d.log[-1] == ("erase", 7)
    ok("evdev FF: wgranie efektu, odtwarzanie, stop, sprzątanie")

    path = os.path.join(TMP, "hidraw-bt")
    open(path, "wb").close()
    d = FFDev(ff=False)
    r = R.Rumble(1, d, "auto", hidraw=(path, True))
    assert r.mode == "hidraw"
    r.set(30, 250)
    f = frames(path)
    assert len(f) == 1 and f[0][3 + 2] == 30 and f[0][3 + 3] == 250, f
    r.set(30, 250); r.set(0, 0)
    f = frames(path)
    assert len(f) == 2 and f[1][5] == 0 and f[1][6] == 0 and f[0][1] != f[1][1], "stop + nowy numer sekwencji"
    r.set(80, 80)
    await asyncio.sleep(R.WATCHDOG_S + 0.2)
    f = frames(path)
    assert f[-1][5] == 0 and f[-1][6] == 0 and r.cur == (0, 0), "brak odświeżeń -> strażnik zatrzymuje silniki"
    r.set(90, 90); await asyncio.sleep(0.5); r.set(90, 90)
    await asyncio.sleep(R.WATCHDOG_S - 0.4)
    assert r.cur == (90, 90), "odświeżana wibracja trwa"
    r.close()
    assert frames(path)[-1][5:7] == b"\x00\x00" and r.fd is None, "zamknięcie zatrzymuje wibrację"
    ok("hidraw: raport przy zmianie, stop, strażnik po ciszy, zamknięcie")

    # ---------- wibracja i kolor w jednym raporcie, powtórka zmiany ----------
    path4 = os.path.join(TMP, "hidraw-lightbar")
    open(path4, "wb").close()
    r = R.Rumble(7, FFDev(ff=False), "auto", hidraw=(path4, True))
    assert r.owns_lightbar
    r.set(200, 255, (255, 255, 255))
    await asyncio.sleep(R.REPEAT_S + 0.03)
    f = frames(path4)
    assert len(f) == 2 and all(x[5:7] == bytes([200, 255]) and x[3 + 44:3 + 47] == b"\xff\xff\xff" for x in f), \
        "zmiana: raport od razu i drugi raz po REPEAT_S, kolor w tym samym raporcie"
    assert f[0][1] != f[1][1], "powtórka z nowym numerem sekwencji"
    r.set(200, 255, (255, 255, 255)); await asyncio.sleep(R.REPEAT_S + 0.03)
    assert len(frames(path4)) == 2, "bez zmian (świeże) - nic"
    r.set(0, 0, (0, 0, 64)); await asyncio.sleep(R.REPEAT_S + 0.03)
    f = frames(path4)
    assert len(f) == 4 and f[-1][5:7] == b"\0\0" and f[-1][3 + 44:3 + 47] == bytes([0, 0, 64])
    r.set(0, 0)
    assert r.rgb == (0, 0, 64), "set bez koloru zostawia ostatni kolor"
    r.close()

    import sender_ctrl
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:ff", FFDev(ff=False), None, None)
    led = os.path.join(TMP, "led"); os.makedirs(led)
    c.led = led
    path5 = os.path.join(TMP, "hidraw-ctrl"); open(path5, "wb").close()
    c.rumble = R.Rumble(8, c.pad_dev, "auto", hidraw=(path5, False))
    c.apply_feedback(10, 20, 1, 2, 3)
    rep = open(path5, "rb").read()[:63]
    assert rep[0] == 0x02 and rep[3:5] == bytes([10, 20]) and rep[1 + 44:1 + 47] == bytes([1, 2, 3])
    assert not os.path.exists(os.path.join(led, "multi_intensity")), "kolor nie idzie przez sterownik (osobny raport = wyścig)"
    c.rumble.close()
    c.rumble = R.Rumble(9, c.pad_dev, "off")
    c.apply_feedback(0, 0, 9, 8, 7)
    assert open(os.path.join(led, "multi_intensity")).read() == "9 8 7", "bez raportu HID - kolor przez sterownik jak dawniej"
    ok("lightbar razem z wibracją w raporcie HID (bez wyścigu ze sterownikiem), powtórka zmiany, sysfs bez hidraw")

    path2 = os.path.join(TMP, "hidraw-fallback")
    open(path2, "wb").close()
    d = FFDev(fail=True)
    r = R.Rumble(2, d, "auto")
    assert r.mode == "evdev"
    R.find_hidraw, orig = (lambda dev, root="/sys": (path2, False)), R.find_hidraw
    r.set(5, 6)
    R.find_hidraw = orig
    assert r.mode == "hidraw" and open(path2, "rb").read()[:1] == b"\x02", "błąd evdev FF -> raport HID (USB)"
    r.close()
    path3 = os.path.join(TMP, "hidraw-both")
    open(path3, "wb").close()
    d = FFDev()
    r = R.Rumble(5, d, "auto", hidraw=(path3, True))
    assert r.methods == ["evdev", "hidraw"] and r.bits == (RUMBLE_EVDEV | RUMBLE_HIDRAW | CAP_TRIGGERS | CAP_TRIG_FX
                                                        | CAP_TRIG_RAW), (r.methods, r.bits)   # spusty tylko przez HID
    r.set(70, 140)
    assert ("upload", 140 * 257, 70 * 257) in d.log and frames(path3)[-1][5:7] == bytes([70, 140]), "obie drogi naraz"
    d.fail = True
    r.set(0, 0); r.set(10, 20)
    assert r.methods == ["hidraw"] and frames(path3)[-1][5:7] == bytes([10, 20]), "błąd sterownika - zostaje raport HID"
    r.close()
    ok("auto: sterownik i raport HID jednocześnie; błąd jednej drogi wyłącza tylko ją")
    from proto import WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OFF, WHY_OK, WHY_OPEN
    find, has = R.find_hidraw, R.kernel_has_hidraw
    R.find_hidraw = lambda dev, root="/sys": (None, False)
    R.kernel_has_hidraw = lambda root="/sys": False
    r = R.Rumble(6, FFDev(ff=False), "auto")
    assert r.bits == 0 and r.why == WHY_NO_HIDRAW, "jądro bez hidraw i bez FF"
    R.kernel_has_hidraw = lambda root="/sys": True
    assert R.Rumble(6, FFDev(ff=False), "auto").why == WHY_NOT_FOUND
    R.find_hidraw, R.kernel_has_hidraw = find, has
    assert R.Rumble(6, FFDev(ff=False), "auto", hidraw=(os.path.join(TMP, "nie-ma", "hidraw9"), True)).why == WHY_OPEN
    def denied(*a):
        raise PermissionError(13, "Permission denied")
    R.os.open, real_open = denied, R.os.open
    assert R.Rumble(6, FFDev(ff=False), "auto", hidraw=(path3, True)).why == WHY_ACCESS, "bez doas"
    R.os.open = real_open
    assert R.Rumble(6, FFDev(ff=False), "auto", hidraw=(path3, True)).why == WHY_OK
    assert R.Rumble(3, FFDev(ff=False), "off").why == WHY_OFF
    ok("powód braku wibracji: jądro bez hidraw, nie znaleziono, brak uprawnień, błąd otwarcia, off")
    assert R.Rumble(3, FFDev(ff=False), "off").mode is None
    assert R.Rumble(4, FFDev(ff=False), "evdev").mode is None
    ok("błąd evdev FF przełącza na hidraw; off / evdev bez FF = bez wibracji")

asyncio.run(main())
print("RUMBLE OK")
