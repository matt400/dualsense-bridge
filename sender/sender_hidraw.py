"""Sender: przycisk mute (mikrofon) DualSense z surowych raportów HID (hidraw).

Sterownik hid-playstation nie wysyła mute przez evdev (sam przełącza nim diodę mikrofonu), więc czytamy go
równolegle z węzła /dev/hidrawN tego samego pada. Każdy czytelnik hidraw dostaje własną kopię raportów -
nic nie zabieramy sterownikowi ani evdev. Odczyt jest nieblokujący (loop.add_reader), a pakiet idzie
tylko przy zmianie stanu przycisku. Brak dostępu do hidraw (uruchomienie bez doas) = pad działa bez mute.

Raport wejściowy: USB 0x01 (dane od bajtu 1), Bluetooth 0x31 (dane od bajtu 2); w danych buttons[2] jest
na pozycji 9, mute = bit 2 (jak DS_BUTTONS2_MIC_MUTE w jądrze).
"""
import asyncio
import glob
import os
import re

MUTE_MASK = 0x04
_OFFSET = {0x01: 1 + 9, 0x31: 2 + 9}     # id raportu -> bajt z buttons[2]
_HID_DIR = re.compile(r"^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$")


def mute_state(report):
    """-> True / False (mute wciśnięty) albo None, gdy to nie pełny raport wejściowy (np. krótki 0x01 po BT)."""
    if not report:
        return None
    off = _OFFSET.get(report[0])
    if off is None or len(report) <= off or (report[0] == 0x01 and len(report) < 64):
        return None
    return bool(report[off] & MUTE_MASK)


def find_hidraw(pad_dev, sys_root="/sys"):
    """Węzeł /dev/hidrawN należący do tego samego urządzenia HID co pad evdev (wspólny katalog VVVV:PPPP:...)."""
    try:
        node = os.path.realpath(f"{sys_root}/class/input/{os.path.basename(pad_dev.path)}")
    except (OSError, AttributeError):
        return None
    hid = node
    while hid not in ("/", "") and not _HID_DIR.match(os.path.basename(hid)):
        hid = os.path.dirname(hid)
    if hid in ("/", ""):
        return None
    found = sorted(glob.glob(os.path.join(hid, "hidraw", "hidraw*")))
    return f"/dev/{os.path.basename(found[0])}" if found else None


async def stream_mute(c, path=None):
    """Czyta raporty hidraw pada i woła c.set_mute() przy zmianie. Kończy się, gdy węzeł zniknie (pad odszedł)."""
    path = path or find_hidraw(c.pad_dev)
    if not path:
        print(f"Controller {c.idx}: mute button unavailable (no hidraw node found)")
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        print(f"Controller {c.idx}: mute button unavailable ({path}: {e.strerror}) - run the sender with doas")
        return
    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def readable():
        try:
            while True:
                data = os.read(fd, 128)
                if not data:
                    raise OSError("hidraw closed")
                m = mute_state(data)
                if m is not None and m != c.mute:
                    c.set_mute(m)
        except BlockingIOError:
            pass
        except OSError as e:
            if not done.done():
                done.set_result(e)

    loop.add_reader(fd, readable)
    print(f"Controller {c.idx}: mute button via {path}")
    try:
        await done
    finally:
        loop.remove_reader(fd)
        os.close(fd)
        if c.mute:
            c.set_mute(False)        # pad zniknął z wciśniętym mute - nie zostawiaj go "trzymanego"
