"""A minimal focusable slider widget (Textual ships none).

Adjust with ←/→ (or ↑/↓), Home/End for the extremes, or click on the track.
Reads its current integer value via ``.value``. An optional ``labels`` mapping
shows a caption for the bucket the value falls into.

Layout: a fixed-width head (``value% caption``) sits on the left and the draggable
track fills the rest to the right edge — so the track's clickable area lines up
exactly with what's drawn (no dead zone past the bar).
"""

from __future__ import annotations

from textual import events
from textual.reactive import reactive
from textual.widget import Widget


class Slider(Widget, can_focus=True):
    DEFAULT_CSS = """
    Slider { height: 1; width: 100%; }
    Slider:focus { color: $accent; text-style: bold; }
    """

    value: reactive[int] = reactive(0)

    def __init__(self, *, value=0, lo=0, hi=100, step=5, labels=None, id=None):
        super().__init__(id=id)
        self._lo, self._hi, self._step = lo, hi, step
        self._labels = labels or {}  # threshold(int) -> caption
        # fixed head width: "100% " + the widest caption (so the track never shifts)
        cap_w = max((len(c) for c in self._labels.values()), default=0)
        self._head = 5 + (cap_w + 1 if cap_w else 0)
        self.set_reactive(Slider.value, self._clamp(value))

    def _clamp(self, v: int) -> int:
        v = max(self._lo, min(self._hi, int(v)))
        return round(v / self._step) * self._step

    def _set(self, v: int) -> None:
        self.value = self._clamp(v)

    def _caption(self) -> str:
        cap = ""
        for threshold in sorted(self._labels):
            if self.value >= threshold:
                cap = self._labels[threshold]
        return cap

    def _track_width(self) -> int:
        return max(self.size.width - self._head, 6)

    def render(self):
        head = f"{self.value:3d}% {self._caption()}".ljust(self._head)
        track = self._track_width()
        frac = (self.value - self._lo) / ((self._hi - self._lo) or 1)
        pos = int(round(frac * (track - 1)))
        bar = "".join("━" if i < pos else ("●" if i == pos else "─") for i in range(track))
        return head + bar

    def on_key(self, event: events.Key) -> None:
        if event.key in ("right", "up"):
            self._set(self.value + self._step); event.stop()
        elif event.key in ("left", "down"):
            self._set(self.value - self._step); event.stop()
        elif event.key == "home":
            self._set(self._lo); event.stop()
        elif event.key == "end":
            self._set(self._hi); event.stop()

    def on_click(self, event: events.Click) -> None:
        self.focus()
        track = self._track_width()
        # map only the track region; clicks on the left head clamp to the minimum
        frac = max(0.0, min(1.0, (event.x - self._head) / max(track - 1, 1)))
        self._set(self._lo + frac * (self._hi - self._lo))
