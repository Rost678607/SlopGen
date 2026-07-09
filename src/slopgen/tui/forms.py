"""Declarative form kit — describe a screen's controls as a list of Field
specs instead of hand-wiring every ``yield Label(...); yield Input(...)`` pair.

A :class:`Form` knows how to
  * **build** its widgets (``compose`` for screens, ``build`` for live mounting),
  * **read** every value back as a ``{key: value}`` dict (typed: numbers come
    back as ``float``/``int``, toggles as ``bool``),
  * **fill** the widgets from a dict (profile prefill), and
  * toggle :class:`Group` visibility from a predicate over the current values
    (and scroll a freshly-revealed group into view).

Widget ids are ``{form.ns}-{field.key}`` so CSS/queries stay stable and
predictable. All labels/placeholders go through the caller's ``t`` translator.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Label, Select, Static, Switch, TextArea

T = Callable[[str], str]  # i18n lookup
Options = Sequence[tuple[str, str]]  # (label, value) pairs (labels already final)


class FieldTextArea(TextArea):
    """The one text field. It self-sizes to its content: 1..5 visible rows,
    growing as you type and scrolling past five. A `single_line` field treats
    Enter as "next field" (no newline) — used for short/medium fields; multi-line
    fields keep Enter and are pinned to five rows by the ``text-field-large`` class."""

    def __init__(self, *args, single_line: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._single = single_line

    def on_mount(self) -> None:
        resize_text_field(self)

    def on_resize(self) -> None:  # width changed → wrapping (and row count) changed
        resize_text_field(self)

    async def _on_key(self, event) -> None:
        if self._single and event.key == "enter":
            event.prevent_default()
            event.stop()
            self.screen.focus_next()
            return
        await super()._on_key(event)  # TextArea inserts text in its async _on_key


class NumStep(Static):
    """A one-glyph ▲/▼ stepper for :class:`Number`. Unlike ``Button`` it has no
    minimum width, so it can render as a ~square 2×1 cell. Clicking posts
    :class:`NumStep.Pressed` with the target input's id and a +1/-1 delta."""

    class Pressed(Message):
        def __init__(self, field_id: str, delta: int):
            self.field_id = field_id
            self.delta = delta
            super().__init__()

    def __init__(self, glyph: str, field_id: str, delta: int, **kwargs):
        super().__init__(glyph, **kwargs)
        self._field_id = field_id
        self._delta = delta

    def on_click(self) -> None:
        self.post_message(self.Pressed(self._field_id, self._delta))


class Field:
    """One entry in a form. Subclasses render a widget and read/fill its value."""

    has_value = True

    def __init__(self, key: str, label: str = ""):
        self.key = key
        self.label = label  # i18n key (resolved via ``t`` at build time)

    def wid(self, ns: str) -> str:
        return f"{ns}-{self.key}"

    # -- rendering --
    def build(self, ns: str, t: T) -> list:
        """Return freshly-constructed widgets (label + control)."""
        raise NotImplementedError

    # -- value access (no-op for decoration fields) --
    def read(self, host, ns: str) -> Any:  # noqa: D401
        return None

    def fill(self, host, ns: str, value: Any) -> None:
        pass

    def _label_widgets(self, t: T) -> list:
        return [Label(t(self.label))] if self.label else []


class Text(Field):
    """A text field.

    Regular fields grow from one to five visible text rows. ``large`` fields use
    a text area that always shows five text rows and scrolls when content is
    longer.
    """

    MAX_ROWS = 5

    def __init__(
        self,
        key,
        label="",
        *,
        value="",
        placeholder="",
        password=False,
        itype="text",
        large=False,
    ):
        super().__init__(key, label)
        self.value = value
        self.placeholder = placeholder
        self.password = password
        self.itype = itype
        self.large = large

    def build(self, ns, t):
        value = "" if self.value is None else str(self.value)
        if self._uses_input:
            return self._label_widgets(t) + [
                Input(
                    value=value,
                    placeholder=t(self.placeholder) if self.placeholder else "",
                    password=self.password,
                    type=self.itype,
                    id=self.wid(ns),
                )
            ]
        # the unified text field: a self-sizing TextArea (grows 1..5, or fixed 5).
        # placeholder is native (rendered dim when empty via text-area--placeholder).
        area = FieldTextArea(text=value, id=self.wid(ns), single_line=not self.large,
                             placeholder=t(self.placeholder) if self.placeholder else "",
                             classes="text-field " + ("text-field-large" if self.large else "text-field-short"))
        # on_mount sizes it once laid out — no build-time resize (width unknown yet)
        return self._label_widgets(t) + [area]

    @property
    def _uses_input(self) -> bool:
        return self.password or self.itype != "text"

    def _input(self, host, ns) -> Input:
        return host.query_one(f"#{self.wid(ns)}", Input)

    def _area(self, host, ns) -> TextArea:
        return host.query_one(f"#{self.wid(ns)}", TextArea)

    def read(self, host, ns) -> str:
        if self._uses_input:
            return self._input(host, ns).value.strip()
        return self._area(host, ns).text.strip()

    def fill(self, host, ns, value) -> None:
        value = "" if value is None else str(value)
        if self._uses_input:
            self._input(host, ns).value = value
            return
        area = self._area(host, ns)
        area.text = value
        resize_text_field(area, large=self.large)


class Range(Field):
    """A slider; :meth:`read` returns an ``int`` in ``[lo, hi]``."""

    def __init__(self, key, label="", *, value=0, lo=0, hi=100, step=5, labels=None):
        super().__init__(key, label)
        self.value, self.lo, self.hi, self.step, self.labels = value, lo, hi, step, labels

    def build(self, ns, t):
        from .slider import Slider

        labels = {k: t(v) for k, v in (self.labels or {}).items()}
        return self._label_widgets(t) + [
            Slider(value=self.value, lo=self.lo, hi=self.hi, step=self.step,
                   labels=labels, id=self.wid(ns))
        ]

    def _slider(self, host, ns):
        from .slider import Slider

        return host.query_one(f"#{self.wid(ns)}", Slider)

    def read(self, host, ns) -> int:
        return int(self._slider(host, ns).value)

    def fill(self, host, ns, value) -> None:
        if value is not None:
            self._slider(host, ns).value = int(value)


class Number(Text):
    """A numeric input; :meth:`read` returns ``float`` (or ``int``), never raises."""

    def __init__(self, key, label="", *, value=None, default=0.0, integer=False):
        super().__init__(key, label, value=value, itype="integer" if integer else "number")
        self.default = default
        self.integer = integer

    def build(self, ns, t):
        wid = self.wid(ns)
        value = "" if self.value is None else str(self.value)
        return self._label_widgets(t) + [
            Horizontal(
                Vertical(
                    NumStep("▲", wid, +1, id=f"{wid}-inc", classes="num-step num-step-up"),
                    NumStep("▼", wid, -1, id=f"{wid}-dec", classes="num-step num-step-down"),
                    classes="num-steps",
                ),
                Input(value=value, type=self.itype, id=wid),
                classes="number-row",
            )
        ]

    def read(self, host, ns):
        raw = super().read(host, ns)
        try:
            num = float(raw)
        except ValueError:
            num = float(self.default)
        return int(num) if self.integer else num


class Choice(Field):
    """A dropdown. ``options`` are ``(label, value)`` pairs with final labels."""

    def __init__(self, key, label="", *, options: Options, value: str | None = None, allow_blank=False):
        super().__init__(key, label)
        self.options = list(options)
        self.value = value
        self.allow_blank = allow_blank

    def build(self, ns, t):
        kwargs: dict[str, Any] = {"allow_blank": self.allow_blank, "id": self.wid(ns)}
        if self.value is not None:
            kwargs["value"] = self.value
        return self._label_widgets(t) + [Select(self.options, **kwargs)]

    def _select(self, host, ns) -> Select:
        return host.query_one(f"#{self.wid(ns)}", Select)

    def read(self, host, ns) -> str:
        v = self._select(host, ns).value
        return "" if v is Select.BLANK else str(v)

    def fill(self, host, ns, value) -> None:
        if value is not None and value != "":
            self._select(host, ns).value = value


class Toggle(Field):
    """A labelled on/off switch (label sits to the right, matching ``switch-row``)."""

    def __init__(self, key, label="", *, value=False):
        super().__init__(key, label)
        self.value = value

    def build(self, ns, t):
        return [
            Horizontal(
                Switch(value=bool(self.value), id=self.wid(ns)),
                Label(t(self.label)),
                classes="switch-row",
            )
        ]

    def _switch(self, host, ns) -> Switch:
        return host.query_one(f"#{self.wid(ns)}", Switch)

    def read(self, host, ns) -> bool:
        return self._switch(host, ns).value

    def fill(self, host, ns, value) -> None:
        self._switch(host, ns).value = bool(value)


class Heading(Field):
    """A section header (no value)."""

    has_value = False

    def __init__(self, label: str):
        super().__init__(f"head-{label}", label)

    def build(self, ns, t):
        return [Static(t(self.label), classes="group-head")]


class Note(Field):
    """A dim hint line (no value)."""

    has_value = False

    def __init__(self, text: str):
        super().__init__(f"note-{text}", text)

    def build(self, ns, t):
        return [Static(t(self.label), classes="hint")]


class Group(Field):
    """A container of fields whose visibility follows ``visible_when(values)``."""

    has_value = False

    def __init__(self, key, fields: Iterable[Field], *, visible_when: Callable[[dict], bool] | None = None):
        super().__init__(key)
        self.fields = list(fields)
        self.visible_when = visible_when

    def build(self, ns, t):
        kids: list = []
        for f in self.fields:
            kids.extend(f.build(ns, t))
        return [Vertical(*kids, id=self.wid(ns), classes="form-group")]

    def value_fields(self) -> list[Field]:
        return [f for f in self.fields if f.has_value]


class Form:
    """A named collection of fields; the unit screens compose/read/fill against."""

    def __init__(self, ns: str, fields: Iterable[Field]):
        self.ns = ns
        self.fields = list(fields)

    # -- rendering --
    def build(self, t: T) -> list:
        out: list = []
        for f in self.fields:
            out.extend(f.build(self.ns, t))
        return out

    def compose(self, t: T):
        yield from self.build(t)

    # -- introspection --
    def value_fields(self) -> list[Field]:
        out: list[Field] = []
        for f in self.fields:
            if isinstance(f, Group):
                out.extend(f.value_fields())
            elif f.has_value:
                out.append(f)
        return out

    def groups(self) -> list[Group]:
        return [f for f in self.fields if isinstance(f, Group)]

    # -- value access --
    def read(self, host) -> dict[str, Any]:
        return {f.key: f.read(host, self.ns) for f in self.value_fields()}

    def fill(self, host, values: dict[str, Any]) -> None:
        for f in self.value_fields():
            if f.key in values:
                f.fill(host, self.ns, values[f.key])

    def refresh_visibility(self, host) -> None:
        """Apply every group's predicate; scroll a newly-shown group into view."""
        if not self.groups():
            return
        values = self.read(host)
        for g in self.groups():
            if g.visible_when is None:
                continue
            node = host.query_one(f"#{g.wid(self.ns)}")
            show = bool(g.visible_when(values))
            was = node.display
            node.display = show
            if show and not was:
                host.call_after_refresh(node.scroll_visible, animate=False)


def resize_text_field(area: TextArea, *, large: bool | None = None) -> None:
    """Size a text field to its content: fixed (``large``) fields always show five
    rows; the rest grow from one to five and scroll past that. The field has no
    border (CSS), so the row count IS the height. Uses the wrapped-line count so
    long lines that wrap count correctly; falls back to logical lines pre-layout."""
    fixed = area.has_class("text-field-large") if large is None else large
    if fixed:
        rows = Text.MAX_ROWS
    else:
        try:
            wrapped = area.wrapped_document.height  # visual rows incl. wrapping
        except Exception:
            wrapped = len(area.text.splitlines()) or 1
        rows = max(1, min(Text.MAX_ROWS, wrapped))
    area.styles.height = rows + 2  # + one background-coloured pad row top & bottom
