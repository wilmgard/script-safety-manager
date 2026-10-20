"""FixOptions editor generated from the dataclass fields, so new core options show up automatically."""
from __future__ import annotations

import dataclasses
import typing

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLineEdit, QSpinBox,
                               QWidget)

from .. import pipeline
from ..pipeline import FixOptions

# Fallback choices for known str fields; the core's own pipeline.CHOICES (when present) wins.
FALLBACK_CHOICES: dict[str, tuple] = {
    "vibration": ("keep", "reduce", "remove"),
    "thin": ("auto", "on", "off"),
    "range_mode": ("scale", "clip"),
    "speed_mode": (None, "shorten_start", "shorten_center", "preserve_anchor"),
    "anchor": ("bottom", "top"),
}

# (min, max, step, decimals) for known numeric fields; unknown ones get a wide default.
NUMBER_RANGES: dict[str, tuple[float, float, float, int]] = {
    "vibration_factor": (0, 1, 0.05, 2),
    "thin_epsilon": (0, 50, 0.5, 1),
    "range_min": (0, 100, 1, 0),
    "range_max": (0, 100, 1, 0),
    "ramp_ms": (0, 60000, 100, 0),
    "ramp_start_factor": (0, 1, 0.05, 2),
    "max_speed": (0, 2000, 10, 0),
}
DEFAULT_RANGE = (0, 1_000_000, 1, 2)
NONE_TEXT = "(profile default)"


def choices() -> dict[str, tuple]:
    return {**FALLBACK_CHOICES, **getattr(pipeline, "CHOICES", {})}


def _resolve(tp) -> tuple[type | None, bool]:
    """Return (base type, optional) for a resolved annotation like `float | None`."""
    args = typing.get_args(tp)
    if args and type(None) in args:
        rest = [a for a in args if a is not type(None)]
        return (rest[0] if len(rest) == 1 else None), True
    return (tp if isinstance(tp, type) else None), False


class OptionsForm(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._getters: dict[str, typing.Callable[[], object]] = {}
        self._setters: dict[str, typing.Callable[[object], None]] = {}
        layout = QFormLayout(self)
        try:
            hints = typing.get_type_hints(FixOptions)
        except Exception:   # unresolvable annotation: fall back to the default value's type
            hints = {}
        known_choices = choices()
        for f in dataclasses.fields(FixOptions):
            default = self._default(f)
            tp, optional = _resolve(hints.get(f.name, type(default)))
            if tp is None and default is not None:
                tp = type(default)
            widget = self._make(f.name, tp, optional, default, known_choices.get(f.name))
            if widget is None:
                continue   # a type we can't edit; the dataclass default is used
            widget.setToolTip(f.name)
            layout.addRow(f.name.replace("_", " "), widget)

    @staticmethod
    def _default(f: dataclasses.Field):
        if f.default is not dataclasses.MISSING:
            return f.default
        if f.default_factory is not dataclasses.MISSING:
            return f.default_factory()
        return None

    def _make(self, name, tp, optional, default, opts):
        if opts is not None:
            w = QComboBox()
            for v in opts:
                w.addItem(NONE_TEXT if v is None else str(v), v)
            self._getters[name] = w.currentData
            self._setters[name] = lambda v, w=w: w.setCurrentIndex(max(0, w.findData(v)))
            w.currentIndexChanged.connect(self.changed)
        elif tp is bool:
            w = QCheckBox()
            self._getters[name] = w.isChecked
            self._setters[name] = lambda v, w=w: w.setChecked(bool(v))
            w.toggled.connect(self.changed)
        elif tp in (int, float):
            lo, hi, step, dec = NUMBER_RANGES.get(name, DEFAULT_RANGE)
            if tp is int:
                w = QSpinBox()
                w.setRange(int(lo), int(hi))
            else:
                w = QDoubleSpinBox()
                w.setDecimals(dec)
                w.setRange(lo, hi)
            w.setSingleStep(step)
            w.setKeyboardTracking(False)
            if optional:   # the minimum stands for None
                w.setSpecialValueText(NONE_TEXT)
                self._getters[name] = lambda w=w, tp=tp: None if w.value() == w.minimum() else tp(w.value())
                self._setters[name] = lambda v, w=w: w.setValue(w.minimum() if v is None else v)
            else:
                self._getters[name] = lambda w=w, tp=tp: tp(w.value())
                self._setters[name] = lambda v, w=w: w.setValue(v)
            w.valueChanged.connect(self.changed)
        elif tp is str:
            w = QLineEdit()
            self._getters[name] = (lambda w=w: w.text() or None) if optional else w.text
            self._setters[name] = lambda v, w=w: w.setText("" if v is None else str(v))
            w.editingFinished.connect(self.changed)
        else:
            return None
        self._setters[name](default)
        return w

    def options(self) -> FixOptions:
        return FixOptions(**{k: get() for k, get in self._getters.items()})

    def set_options(self, opts: FixOptions) -> None:
        self.blockSignals(True)
        try:
            for k, set_ in self._setters.items():
                set_(getattr(opts, k))
        finally:
            self.blockSignals(False)
        self.changed.emit()

    def reset(self) -> None:
        self.set_options(FixOptions())
