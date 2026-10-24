"""Receiver: the accessibility wizard of the Test view - measure drift at rest and how far a stick can be pushed.

ReachRun, stepped by the window clock like rx_drift.DriftRun: for REACH_S seconds every state sample from rx_net is collected
(S.probe) while the player circles the stick as far as is comfortable; rx_access.analyze_reach turns the samples into the
proposed range. No raw-stick mode is needed - only the rim of the travel matters, the sender's own deadzone does not touch it.

Wizard chains the steps for the chosen sticks: "rest" (rx_drift.DriftRun, a dead zone proposal for both sticks), then for each
stick a short "ready" pause (time to pick the pad up) and "reach" (ReachRun). The result is a proposal {stick: {"dz", "outer"}}
that the window shows as the default values of its sliders; a step that cannot give a result ends the wizard with a Failure
(the stick moved at rest, the circle was not completed, ...) instead of a guess.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

import rx_access
import rx_drift as D
import rx_state as S

REACH_S: Final = 6.0
GET_READY_S: Final = 2.0    # between the rest measurement and the reach one: time to pick the pad up
EPS: Final = 1e-6          # the same float-boundary guard as in rx_drift: (t0 + x) - t0 can come out slightly below x


class ReachRun:
    """One reach measurement for a slot: collecting for REACH_S seconds, then the samples."""

    def __init__(self, slot: int, now: float) -> None:
        """Start collecting at time `now` (the state that is already there counts too)."""
        self.slot = slot
        self.t0 = now
        self.done = False
        with S.lock:
            S.probe[slot] = [S.states[slot]["rawst"]]

    def progress(self, now: float) -> float:
        """Fraction of the measurement that has elapsed, 0..1 - drives the progress bar."""
        return min(1.0, max(0.0, (now - self.t0) / REACH_S))

    def tick(self, now: float) -> list[tuple[int, ...]] | None:
        """Return the samples once REACH_S has elapsed (the run is then over), otherwise None."""
        if self.done or now - self.t0 < REACH_S - EPS:
            return None
        return self._stop()

    def _stop(self) -> list[tuple[int, ...]]:
        """Stop collecting and take the samples."""
        with S.lock:
            samples, S.probe[self.slot] = S.probe[self.slot] or [], None
        self.done = True
        return samples

    def cancel(self) -> None:
        """Abort a running measurement (no result); safe to call repeatedly."""
        if not self.done:
            self._stop()


@dataclass(frozen=True, slots=True)
class Failure:
    """Why the wizard stopped: reason is "nodata" | "moved" (rest step) | "few" | "small" (reach step, `stick` says which)."""

    reason: str
    stick: str | None = None


class Wizard:
    """The wizard for one slot, stepped by the window clock: tick(now) until `done`, then `proposal` or `failure`."""

    def __init__(self, slot: int, sticks: Sequence[str], now: float, raw: Callable[[int, int], None] | None = None) -> None:
        """Start at time `now` for the given sticks ("left" / "right"); `raw` is the command that switches the sender to raw sticks."""
        self.slot, self.sticks = slot, tuple(sticks)
        self.steps = [("rest", "")] + [(step, st) for st in self.sticks for step in ("ready", "reach")]
        self.proposal: dict[str, dict[str, float]] = {}
        self.failure: Failure | None = None
        self.done = False
        self._i = -1
        self._t0 = now
        self._drift: D.DriftRun | None = None
        self._reach: ReachRun | None = None
        self._raw = raw
        self._next(now)

    @property
    def step(self) -> str:
        """The current step: "rest" | "ready" | "reach" ("" when done)."""
        return "" if self.done else self.steps[self._i][0]

    @property
    def stick(self) -> str:
        """The stick of the current step ("" for "rest" and when done)."""
        return "" if self.done else self.steps[self._i][1]

    def progress(self, now: float) -> float:
        """Fraction of the current step that has elapsed, 0..1 - drives the progress bar."""
        if self.done:
            return 1.0
        if self._drift is not None and self.step == "rest":
            return self._drift.progress(now)
        if self._reach is not None and self.step == "reach":
            return self._reach.progress(now)
        return min(1.0, max(0.0, (now - self._t0) / GET_READY_S))

    def _next(self, now: float) -> None:
        """Enter the next step (or finish)."""
        self._i += 1
        self._t0 = now
        if self._i >= len(self.steps):
            self.done = True
            return
        step = self.steps[self._i][0]
        if step == "rest":
            self._drift = D.DriftRun(self.slot, now, self._raw)
        elif step == "reach":
            self._reach = ReachRun(self.slot, now)

    def _fail(self, reason: str, stick: str | None = None) -> None:
        """End the wizard with a failure."""
        self.failure, self.done = Failure(reason, stick), True

    def tick(self, now: float) -> None:
        """Advance the current step to time `now`; call from the window clock until `done`."""
        if self.done:
            return
        step, stick = self.steps[self._i]
        if step == "rest":
            self._tick_rest(now)
        elif step == "ready":
            if now - self._t0 >= GET_READY_S - EPS:
                self._next(now)
        elif self._reach is not None and (samples := self._reach.tick(now)) is not None:
            r = rx_access.analyze_reach(samples, stick)
            if r.status != "ok":
                self._fail(r.status, stick)
                return
            self.proposal[stick]["outer"] = r.outer
            self._next(now)

    def _tick_rest(self, now: float) -> None:
        """The rest step: the drift measurement gives a dead zone proposal for every chosen stick."""
        if self._drift is None:
            return
        res = self._drift.tick(now)
        if res is None and self._drift.phase != "done":
            return
        if res is None:
            self._fail("nodata")
        elif res.moved:
            self._fail("moved")
        else:
            self.proposal = {st: {"dz": res.stick(st).dz} for st in self.sticks}
            self._next(now)

    def cancel(self) -> None:
        """Abort the wizard (the pad left, another pad was picked, the view was closed); safe to call repeatedly."""
        if self._drift is not None:
            self._drift.cancel()
        if self._reach is not None:
            self._reach.cancel()
        self.done = True
