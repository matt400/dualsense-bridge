"""Sender: a minimal libopus encoder (ctypes) for the pad's speaker.

The speaker takes 48 kHz mono audio as Opus frames of 10 ms inside HID report 0x36 (see sender_speaker). libopus is loaded lazily and
only through ctypes, so the sender keeps working - without the speaker - on a device where it is not installed: ``available()``
says so (once, in the log) and the sender then does not announce CAP_SPEAKER to the receiver.

The encoder is constant bitrate with a fixed frame size (the pad's decoder is fed one frame per report), so every packet has the
same length. It works in place: the caller writes the 960 bytes of PCM into ``pcm`` and the packet is encoded straight into the
report buffer that was given to ``bind`` - no allocation per frame.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import threading
from typing import Final

from proto import SPK_BYTES, SPK_FRAME, SPK_RATE

OPUS_APPLICATION_AUDIO: Final = 2049
OPUS_SET_BITRATE, OPUS_SET_VBR, OPUS_SET_COMPLEXITY = 4002, 4006, 4010   # opus_defines.h
OPUS_MAX_PACKET: Final = 1275                                           # the largest packet libopus can produce
FRAMES_PER_S: Final = 100                                               # 10 ms frames: a CBR frame of N bytes is N * 8 * 100 bit/s
OPUS_BYTES_MIN, OPUS_BYTES_MAX = 30, 200                                # sane range of bytes per frame (24 .. 160 kbit/s)
COMPLEXITY: Final = 5       # P3a: 0.43 ms per frame on the OnePlus 6 (3-5 % of a core); 10 would cost 0.56 ms for no audible gain
LIB_NAMES: Final = ("libopus.so.0", "libopus.so", "libopus.0.dylib")

_lock = threading.Lock()
_lib: ctypes.CDLL | None = None
_tried = False


def _load() -> ctypes.CDLL | None:
    """Find libopus and declare the few functions used; None when it is not installed."""
    names = [*LIB_NAMES, ctypes.util.find_library("opus") or ""]
    for name in filter(None, names):
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_encoder_ctl.restype = ctypes.c_int
        lib.opus_encode.restype = ctypes.c_int32
        lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int32]
        lib.opus_encoder_destroy.restype = None
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        return lib
    return None


def library() -> ctypes.CDLL | None:
    """The loaded libopus, or None. The first call loads it and logs one line if it is missing."""
    global _lib, _tried
    with _lock:
        if not _tried:
            _tried = True
            _lib = _load()
            if _lib is None:
                print("Speaker: libopus not found - the pad's speaker is off (install the opus package to use it)")
        return _lib


def available() -> bool:
    """Whether the speaker can be offered to the receiver (libopus is installed)."""
    return library() is not None


class OpusEncoder:
    """A mono 48 kHz CBR Opus encoder for 10 ms frames that encodes in place.

    Attributes:
        size: Bytes of every packet (including the TOC byte).
        pcm: Where the caller puts the next frame: SPK_BYTES of signed 16-bit little endian samples.
    """

    def __init__(self, size: int) -> None:
        """Create the encoder.

        Args:
            size: Bytes per 10 ms packet (the bitrate is chosen to match); clamped to OPUS_BYTES_MIN..OPUS_BYTES_MAX.

        Raises:
            OSError: libopus is missing or refused to create / configure the encoder.
        """
        lib = library()
        if lib is None:
            raise OSError("libopus not found")
        self._lib = lib
        self.size = max(OPUS_BYTES_MIN, min(OPUS_BYTES_MAX, size))
        err = ctypes.c_int(0)
        state = lib.opus_encoder_create(SPK_RATE, 1, OPUS_APPLICATION_AUDIO, ctypes.byref(err))
        if err.value != 0 or not state:
            raise OSError(f"opus_encoder_create failed ({err.value})")
        self._state = ctypes.c_void_p(state)
        for request, value in ((OPUS_SET_BITRATE, self.size * 8 * FRAMES_PER_S), (OPUS_SET_VBR, 0), (OPUS_SET_COMPLEXITY, COMPLEXITY)):
            if lib.opus_encoder_ctl(self._state, ctypes.c_int(request), ctypes.c_int32(value)) != 0:
                self.close()
                raise OSError(f"opus_encoder_ctl({request}, {value}) failed")
        self._pcm = bytearray(SPK_BYTES)
        self.pcm = memoryview(self._pcm)
        self._pcm_addr = ctypes.addressof((ctypes.c_char * SPK_BYTES).from_buffer(self._pcm))
        self._out_addr = 0
        self._out_keep: object = None

    def bind(self, out: bytearray, at: int) -> None:
        """Choose where packets go: ``size`` bytes at offset ``at`` of ``out`` (kept alive and pinned by this object).

        Args:
            out: The report buffer.
            at: Offset of the packet inside it.
        """
        if at + self.size > len(out):
            raise ValueError("the packet does not fit into the buffer")
        arr = (ctypes.c_char * len(out)).from_buffer(out)
        self._out_keep = arr
        self._out_addr = ctypes.addressof(arr) + at

    def encode(self) -> bool:
        """Encode the frame in ``pcm`` into the bound buffer.

        Returns:
            True when the packet has exactly ``size`` bytes (always, with CBR); False on an encoder error or another length -
            the caller then sends silence rather than a packet of the wrong size.
        """
        n = self._lib.opus_encode(self._state, self._pcm_addr, SPK_FRAME, self._out_addr, self.size)
        return n == self.size

    def close(self) -> None:
        """Free the encoder (safe to call twice)."""
        if getattr(self, "_state", None):
            self._lib.opus_encoder_destroy(self._state)
            self._state = ctypes.c_void_p(0)
