"""Stable-frame gating, plus shared locks for already-clicked screen regions."""
from dataclasses import dataclass


@dataclass
class _State:
    position: tuple[int, int] | None = None
    stable_frames: int = 0
    last_click: float = float("-inf")
    missing_frames: int = 0


class DetectionGate:
    def __init__(self, stable_frames=2, tolerance=8, cooldown=5.0):
        self.required = stable_frames
        self.tolerance = tolerance
        self.cooldown = cooldown
        self.states = {}

    def _near(self, a, b):
        return a is not None and b is not None and max(abs(a[0]-b[0]), abs(a[1]-b[1])) <= self.tolerance

    def observe(self, key, position, now):
        """Return (ready, False). The separate ledger owns disappearance locks."""
        state = self.states.setdefault(key, _State())
        if position is None:
            state.position = None
            state.stable_frames = 0
            state.missing_frames += 1
            return False, False

        state.missing_frames = 0
        state.stable_frames = state.stable_frames + 1 if self._near(position, state.position) else 1
        state.position = position
        return state.stable_frames >= self.required and now-state.last_click >= self.cooldown, False

    def clicked(self, key, position, now):
        state = self.states.setdefault(key, _State())
        state.last_click = now
        state.stable_frames = 0
        state.missing_frames = 0

    def reset_observations(self):
        """Pausing clears frame evidence; the separate ledger retains click locks."""
        for state in self.states.values():
            state.position = None
            state.stable_frames = 0
            state.missing_frames = 0


@dataclass(eq=False)
class ClickLock:
    window_key: tuple[int, int]
    box: tuple[int, int, int, int]
    template: object
    missing_frames: int = 0


class ClickLedger:
    """Share clicked regions across templates; unknown/occluded pixels never rearm."""
    def __init__(self):
        self.locks: list[ClickLock] = []

    def blocked(self, window_key, box):
        x, y, w, h = box
        for lock in self.locks:
            if lock.window_key != window_key:
                continue
            a, b, c, d = lock.box
            intersection = max(0, min(x+w, a+c)-max(x, a)) * max(0, min(y+h, b+d)-max(y, b))
            if intersection >= .5 * min(w*h, c*d):
                return True
        return False

    def clicked(self, window_key, box, template):
        if not self.blocked(window_key, box):
            self.locks.append(ClickLock(window_key, box, template))

    def observe(self, lock, present):
        """present=None means occluded/unknown; False means known disappearance."""
        lock.missing_frames = lock.missing_frames+1 if present is False else 0
        if lock.missing_frames >= 2:
            self.locks.remove(lock)
            return True
        return False
