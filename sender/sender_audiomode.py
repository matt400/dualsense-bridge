"""Sender: the audio mode of a pad's actuators - HD haptics and the speaker - as a mixin of ``sender_rumble.Rumble``.

On a Bluetooth DualSense the actuators play audio once a setup report (0x31) switches them over, and any report with a rumble
bit (valid_flag0 0x01 / 0x02) takes them back. The streams themselves live elsewhere (``sender_haptics.HapticsStream``,
``sender_speaker.SpeakerStream``); this part keeps the pad in the right mode around them: it enters audio mode for the first
stream, leaves it after the last one, writes the speaker volume, and owns the policy for a game's motors during a stream
(``haptics_motors``: the haptics win and the motors wait, or the motors pause the haptics until they stop).

``Rumble`` owns the file descriptor, the writer thread and the report sequence counter, so every audio write goes through its
``_write``. The mixin reaches back into it only through the attributes and hooks declared under TYPE_CHECKING below: ``hid``
(the HID report way works), ``_send`` (a motor report on every way), ``_refresh_hid`` (resend the current state over HID) and
``_hid_failed`` (count an error of the HID way).
"""

import time
from typing import TYPE_CHECKING, Final, Protocol

from sender_hidwrite import AUDIO_PENDING, FATAL_ERRNO, KIND_AUDIO, HidWriter
from sender_report import BT_SEQ_MASK, BYTE_MASK, COMMON_LEN, VALID_FLAG0_AT, _bt_report, haptics_setup_report
from sender_tick import call_later

if TYPE_CHECKING:
    import asyncio

    import sender_selftest as probe

MOTORS_HAPTICS, MOTORS_RUMBLE = "haptics", "rumble"   # haptics_motors: the haptics win (log once) / the game's rumble wins
HAPTICS_RESEND_S: Final = 0.05              # the setup report goes out a second time this much later (see Rumble._write_setup)
HAPTICS_FAIL_MAX: Final = 50                # this many write errors in a row (~0.5 s of the stream) end the stream, not the HID way
FLAG0_SPEAKER_VOLUME: Final = 0x20          # valid_flag0: the speaker volume byte of this report (P3a: safe on its own)
SPEAKER_VOLUME_AT: Final = 5                # the speaker volume in the common part, 0..255 (0 = silent)


class HapticsReport(Protocol):
    """A Bluetooth output report that carries haptics audio (0x32 now; a 0x33..0x36 variant or the speaker report may follow)."""

    def finish(self, seq: int) -> bytes | bytearray | memoryview:
        """Complete the report with the sequence number (and its own counter and CRC) and return the bytes to write."""
        ...


class AudioMode:
    """Audio mode of one pad (haptics and speaker streams); mixed into Rumble, which calls ``_init_audio`` from its constructor.

    Attributes:
        motor_policy: ``MOTORS_HAPTICS`` or ``MOTORS_RUMBLE`` (the ``haptics_motors`` setting).
        haptics: A haptics stream is running (between haptics_begin and haptics_end): reports carry no rumble bits.
        speaker: A speaker stream is running: the pad is in audio mode for it, like for haptics.
        t_audio: When the last audio report was written (monotonic): the speaker follows this clock.
        ducked: Policy "rumble" only: a game's motors paused the haptics, they come back when the motors stop.
    """

    if TYPE_CHECKING:                       # provided by Rumble
        idx: int
        fd: int | None
        seq: int
        bt: bool
        cur: tuple[int, int]
        _writer: HidWriter | None

        @property
        def hid(self) -> bool:
            """Whether the HID report way works."""
        def _write(self, data: bytes | bytearray | memoryview, kind: str, reused: bool = ..., limit: int = ...,
                   tag: "probe.FbRecord | None" = ...) -> None: ...
        def _send(self, small: int, large: int) -> None: ...
        def _refresh_hid(self) -> None: ...
        def _hid_failed(self, error: OSError) -> None: ...

    def _init_audio(self, haptics_motors: str) -> None:
        """Start in the normal (non-audio) mode with the given motor policy."""
        self.motor_policy = haptics_motors
        self.haptics = False
        self.speaker = False
        self.t_audio = 0.0
        self.ducked = False
        self._resume = False                      # the report being sent stops the motors of a paused stream: audio mode follows it
        self._motors_noted = False                # "game rumble ignored" is logged once per stream
        self._haptics_fails = 0                   # audio writes that failed in a row (HAPTICS_FAIL_MAX)
        self._haptics_timer: asyncio.TimerHandle | None = None   # the second setup report (HAPTICS_RESEND_S)

    def _close_audio(self) -> None:
        """End every stream before the final reports of Rumble.close: back to the normal mode, the resend timer cancelled."""
        if self.haptics or self.speaker:
            self.haptics = self.speaker = False
            self._leave_audio()
        if self._haptics_timer is not None:
            self._haptics_timer.cancel()
            self._haptics_timer = None

    @property
    def audio(self) -> bool:
        """Whether reports must keep the actuators in audio mode: a haptics or speaker stream runs, no game motors paused it."""
        return (self.haptics or self.speaker) and not self.ducked

    def _audio_for(self, small: int, large: int) -> bool:
        """Decide whether a report with these motor values goes out in the audio flavour (no rumble bits).

        Policy "haptics": always while the stream runs; the first motor request of a stream is logged. Policy "rumble": motors
        above zero pause the haptics (``ducked``: normal reports), and so does the report that stops them - ``_resume`` then
        puts the pad back into audio mode right after it (see _send).
        """
        if not (self.haptics or self.speaker):
            return False
        motors = bool(small or large)
        if self.motor_policy == MOTORS_RUMBLE:
            if motors and not self.ducked:
                self.ducked = True
                print(f"Controller {self.idx}: game rumble pauses the haptics until the motors stop (haptics_motors = rumble)")
            elif self.ducked and not motors:
                self._resume = True
        elif motors and not self._motors_noted:
            self._motors_noted = True
            print(f"Controller {self.idx}: game rumble ignored while haptics play (haptics_motors = haptics)")
        return not self.ducked

    def haptics_begin(self) -> bool:
        """Put the pad into audio-haptics mode for a stream (HapticsStream calls it with the first chunk).

        Motors that are running are stopped first: once reports carry no rumble bits their stop would never arrive. The game's
        value stays in ``cur`` and haptics_end applies it again. Policy "rumble" with the motors running starts paused instead.

        Returns:
            True when the pad is in audio mode (or waits for the motors to stop); False when it cannot play haptics (USB, no HID
            report).
        """
        if self.haptics:
            return True
        if not self._can_audio():
            return False
        if self.speaker:                    # already in audio mode: the speaker's setup report is in effect
            self.haptics = True
            return True
        self._enter_audio(haptics=True)
        return True

    def _can_audio(self) -> bool:
        """Whether the pad can take audio reports: a Bluetooth pad with a working HID report way."""
        return self.hid and self.fd is not None and self.bt

    def _enter_audio(self, haptics: bool) -> None:
        """Switch the pad from the normal mode to audio mode for the first stream (haptics or speaker).

        Args:
            haptics: True for a haptics stream, False for a speaker stream.
        """
        rumbling = any(self.cur)
        if rumbling and self.motor_policy != MOTORS_RUMBLE:
            self._send(0, 0)        # before the flags below: this report must still carry the stop
        self.haptics, self.speaker = haptics, not haptics
        self.ducked = rumbling and self.motor_policy == MOTORS_RUMBLE
        self._motors_noted, self._haptics_fails = False, 0
        if not self.ducked:
            self._write_setup()

    def speaker_begin(self, volume: int) -> bool:
        """Put the pad into audio mode for a speaker stream and set the speaker volume (SpeakerStream calls it with the first frame).

        Args:
            volume: The speaker volume byte, 0..255.

        Returns:
            True when the pad is in audio mode; False when it cannot play audio (USB, no HID report).
        """
        if self.speaker:
            return True
        if not self._can_audio():
            return False
        if self.haptics:                    # already in audio mode: the haptics setup report is in effect
            self.speaker = True
        else:
            self._enter_audio(haptics=False)
        self._write_volume(volume)
        return True

    def speaker_end(self) -> None:
        """End a speaker stream: the volume goes to 0 first (it removed the pop at the end of a stream in P3a), then the normal mode."""
        if not self.speaker:
            return
        self.speaker = False
        self._write_volume(0)
        if not self.haptics:
            self._leave_audio()

    def _write_volume(self, volume: int) -> None:
        """Write a report that only sets the speaker volume (valid_flag0 0x20: no rumble bit, so audio mode is not left)."""
        if self.fd is None:
            return
        common = bytearray(COMMON_LEN)
        common[VALID_FLAG0_AT] = FLAG0_SPEAKER_VOLUME
        common[SPEAKER_VOLUME_AT] = max(0, min(BYTE_MASK, volume))
        self.seq = (self.seq + 1) & BT_SEQ_MASK
        try:
            self._write(_bt_report(common, self.seq), KIND_AUDIO)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as e:
            self._haptics_failed(e)

    def _write_setup(self) -> None:
        """Send the setup report now and once more HAPTICS_RESEND_S later.

        The second copy covers a lost report and the kernel driver's own rumble report that may still be queued behind the
        one that stopped the motors: re-sending the setup report brought the haptics back every time in the P0 measurements.
        """
        self._setup_report()
        if self._haptics_timer is not None:
            self._haptics_timer.cancel()
        self._haptics_timer = call_later(HAPTICS_RESEND_S, self._resend_setup)

    def _setup_report(self) -> None:
        """Write one setup report; a full queue is not an error (the second copy follows)."""
        if self.fd is None:
            return
        self.seq = (self.seq + 1) & BT_SEQ_MASK
        try:
            self._write(haptics_setup_report(self.seq), KIND_AUDIO)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as e:
            self._haptics_failed(e)

    def _resend_setup(self) -> None:
        """Timer callback of _write_setup: the second setup report, unless the stream ended or the motors paused it meanwhile."""
        self._haptics_timer = None
        if self.audio:
            self._setup_report()

    def write_haptics(self, report: HapticsReport) -> bool:
        """Write one haptics report (the stream's tick); Rumble owns the file descriptor and the sequence counter.

        Args:
            report: The report; ``finish(seq)`` gives the bytes to write.

        Returns:
            True when the report was written; False when it was not (no stream, paused by the game's motors, a full queue, an
            error). A run of errors ends the stream (``haptics`` becomes False), but never switches the HID way off.
        """
        if not self.audio or self.fd is None:
            return False
        self.seq = (self.seq + 1) & BT_SEQ_MASK
        try:
            self._write(report.finish(self.seq), KIND_AUDIO, reused=True, limit=AUDIO_PENDING)
        except (BlockingIOError, InterruptedError):
            return False
        except OSError as e:
            self._haptics_failed(e)
            return False
        if self._writer is None:              # queued: the writer thread reports the outcome (_write_failed / _write_ok)
            self._haptics_fails = 0
        self.t_audio = time.monotonic()
        return True

    def _haptics_failed(self, error: OSError) -> None:
        """Count a failed haptics write: a pad that is gone goes through _failed, a long run of errors ends only the stream."""
        self._haptics_fails += 1
        if error.errno in FATAL_ERRNO:
            self.haptics = self.speaker = self.ducked = False
            self._hid_failed(error)
        elif self._haptics_fails >= HAPTICS_FAIL_MAX:
            print(f"Controller {self.idx}: audio stopped after {self._haptics_fails} write errors ({error!r})")
            self.haptics = self.speaker = False
            self._leave_audio()
        elif self._haptics_fails == 1:
            print(f"Controller {self.idx}: haptics write failed ({error!r}) - retrying")

    def haptics_end(self) -> None:
        """Leave audio-haptics mode: a normal report (rumble, lightbar and trigger state) takes the actuators back from audio."""
        if not self.haptics:
            return
        self.haptics = False
        if not self.speaker:                # a running speaker stream keeps the pad in audio mode
            self._leave_audio()

    def _leave_audio(self) -> None:
        """Back to the normal mode after the last audio stream: a normal report brings rumble, lightbar and triggers back."""
        self.ducked = self._resume = False
        if self._haptics_timer is not None:
            self._haptics_timer.cancel()
            self._haptics_timer = None
        self._refresh_hid()

