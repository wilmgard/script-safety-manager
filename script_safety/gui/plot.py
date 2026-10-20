"""Before/after position plot with over-speed tint and a speed heatmap strip."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF

from ..model import Action

ORIGINAL_PEN = pg.mkPen("#8a8a8a", width=1)
FIXED_PEN = pg.mkPen("#1f8fd6", width=1.6)
OVERSPEED_PEN = pg.mkPen((255, 60, 60, 170), width=4)
HEAT_BIN_S = 0.02          # heatmap resolution
HEAT_MAX_FACTOR = 1.5      # speed / max_speed that maps to the top of the colour scale

# 0 = still, 1/1.5 = exactly the profile limit, above that is over-speed.
HEAT_MAP = pg.ColorMap(
    [0.0, 0.35, 1 / HEAT_MAX_FACTOR - 0.001, 1 / HEAT_MAX_FACTOR + 0.001, 1.0],
    [(20, 30, 60), (40, 140, 90), (240, 220, 60), (255, 70, 50), (160, 0, 0)])


def arrays(actions: list[Action]) -> tuple[np.ndarray, np.ndarray]:
    t = np.fromiter((a.at for a in actions), float, len(actions)) / 1000.0
    p = np.fromiter((a.pos for a in actions), float, len(actions))
    return t, p


def segment_speeds(t: np.ndarray, p: np.ndarray) -> np.ndarray:
    """Units/second per segment (t in seconds); zero-length moves count as infinite, like model.speed."""
    dt = np.diff(t)
    dp = np.abs(np.diff(p))
    with np.errstate(divide="ignore", invalid="ignore"):
        s = dp / dt
    s[dt <= 0] = np.where(dp[dt <= 0] > 0, np.inf, 0.0)
    return s


def heat_row(t: np.ndarray, s: np.ndarray, n_bins: int) -> np.ndarray:
    """Peak speed per time bin, so short fast moves are never averaged away."""
    row = np.zeros(n_bins)
    if len(s) == 0:
        return row
    b0 = np.clip((t[:-1] / HEAT_BIN_S).astype(int), 0, n_bins - 1)
    b1 = np.clip((t[1:] / HEAT_BIN_S).astype(int), 0, n_bins - 1)
    for i in np.argsort(s, kind="stable"):   # ascending, so the fastest segment wins shared bins
        row[b0[i]:b1[i] + 1] = s[i]
    return row


class TimeAxis(pg.AxisItem):
    """Seconds shown as m:ss, the way people read video time."""

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            v = max(0.0, v)
            m, s = divmod(v, 60)
            out.append(f"{int(m)}:{s:04.1f}" if spacing < 1 else f"{int(m)}:{int(s):02d}")
        return out


class ScriptPlot(pg.GraphicsLayoutWidget):
    def __init__(self, parent=None, show_heat: bool = True):
        super().__init__(parent)
        # Follow the window colours (light or dark) instead of pyqtgraph's default black.
        pal = self.palette()
        self.setBackground(pal.base().color())
        self._axis_pen = pg.mkPen(pal.text().color())
        self.main = self.addPlot(row=0, col=0, axisItems={"bottom": TimeAxis("bottom")})
        self.main.setLabel("left", "position")
        self.main.setYRange(-2, 102, padding=0)
        self.main.setLimits(yMin=-10, yMax=110, xMin=-5)
        self.main.setMouseEnabled(x=True, y=False)
        self.main.showGrid(x=True, y=True, alpha=0.15)
        self.main.hideButtons()   # the small "A" auto-range button only confuses first-time users
        self.main.addLegend(offset=(10, 5))
        self.overspeed = self.main.plot([], [], pen=OVERSPEED_PEN, connect="pairs", name="too fast (original)")
        self.original = self.main.plot([], [], pen=ORIGINAL_PEN, name="original")
        self.fixed = self.main.plot([], [], pen=FIXED_PEN, name="fixed")
        self.highlight = pg.LinearRegionItem(movable=False, brush=(255, 200, 0, 50), pen=pg.mkPen(None))
        self.highlight.setZValue(-10)
        self.highlight.hide()
        self.main.addItem(self.highlight)
        for item in (self.original, self.fixed):
            item.setClipToView(True)
            item.setDownsampling(auto=True, method="peak")

        self.heat = self.addPlot(row=1, col=0, axisItems={"bottom": TimeAxis("bottom")})
        self.heat.setVisible(show_heat)
        self.main.getAxis("bottom").setStyle(showValues=not show_heat)
        self.heat.setMaximumHeight(55)
        self.heat.setXLink(self.main)
        self.heat.setMouseEnabled(x=True, y=False)
        self.heat.setYRange(0, 2, padding=0)
        self.heat.setLabel("bottom", "time", units="s")
        self.heat.getAxis("left").setTicks([[(1.5, "orig"), (0.5, "fixed")]])
        self.main.getAxis("left").setWidth(45)
        for plot in (self.main, self.heat):
            for name in ("left", "bottom"):
                plot.getAxis(name).setPen(self._axis_pen)
                plot.getAxis(name).setTextPen(self._axis_pen)
        self.heat.getAxis("left").setWidth(45)
        self.heat_img = pg.ImageItem(axisOrder="row-major")
        self.heat.addItem(self.heat_img)

    def set_data(self, original: list[Action], fixed: list[Action] | None, max_speed: float,
                 reset_view: bool = False) -> None:
        to, po = arrays(original)
        so = segment_speeds(to, po)
        fast = np.flatnonzero(so > max_speed)
        self.overspeed.setData(np.column_stack([to[fast], to[fast + 1]]).ravel(),
                               np.column_stack([po[fast], po[fast + 1]]).ravel())
        self.original.setData(to, po)
        tf, pf = arrays(fixed) if fixed else (np.empty(0), np.empty(0))
        self.fixed.setData(tf, pf)

        duration = max(to[-1] if len(to) else 0, tf[-1] if len(tf) else 0, 1.0)
        n = int(duration / HEAT_BIN_S) + 1
        rows = np.vstack([heat_row(tf, segment_speeds(tf, pf), n), heat_row(to, so, n)])
        norm = np.clip(np.nan_to_num(rows / (max_speed * HEAT_MAX_FACTOR), posinf=1.0), 0, 1)
        self.heat_img.setImage(HEAT_MAP.map(norm, mode="byte"), autoLevels=False)
        self.heat_img.setRect(QRectF(0, 0, n * HEAT_BIN_S, 2))
        self.main.setLimits(xMax=duration + 5)
        if reset_view:
            self.highlight.hide()
            self.main.setXRange(0, duration, padding=0.01)

    def zoom_to(self, start_ms: int, end_ms: int) -> None:
        span = max(end_ms - start_ms, 500)
        pad = max(span * 0.5, 1000)
        self.main.setXRange((start_ms - pad) / 1000, (end_ms + pad) / 1000, padding=0)
        self.highlight.setRegion((start_ms / 1000, max(end_ms, start_ms + 100) / 1000))
        self.highlight.show()
