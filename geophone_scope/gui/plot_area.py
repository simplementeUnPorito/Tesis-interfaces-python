"""gui/plot_area.py — pyqtgraph real-time multi-channel plot widget."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy

import config


# Global pyqtgraph appearance settings (applied once at import time)
pg.setConfigOptions(antialias=False, useOpenGL=False, background="k", foreground="w",
                    leftButtonPan=True)


class PlotArea(QWidget):
    """
    Stacked pyqtgraph PlotWidgets — one per active slave.

    Each plot shows two curves:
        - raw   (blue)
        - filt  (red, only when a FIR filter is configured)

    Call update_plots() from the render QTimer in the main thread.
    """

    RAW_PEN        = pg.mkPen(color=(80, 140, 255), width=1)
    FILT_PEN       = pg.mkPen(color=(255, 80,  80), width=1)
    RAW_PEN_LIGHT  = pg.mkPen(color=(0,  80, 200),  width=1)
    FILT_PEN_LIGHT = pg.mkPen(color=(200, 40,  40), width=1)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

        # One PlotWidget per MAX_NODES slot (hidden if inactive)
        self._plots:      list[pg.PlotWidget]   = []
        self._raw_curves: list[pg.PlotDataItem]  = []
        self._filt_curves: list[pg.PlotDataItem] = []

        self._n_active: int = 0        # currently visible plot count
        self._disp_samp: int = config.DISP_SAMP

        self._build_plots()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_display_samples(self, n: int) -> None:
        """Change the width of the X-axis window (in samples)."""
        self._disp_samp = max(1, n)

    def set_active_nodes(self, node_indices: list[int]) -> None:
        """
        Show only the given node indices in the specified order (Hammer→Geo1→Geo2).
        Reorders the layout widgets to match.
        """
        self._n_active = len(node_indices)
        # Quitar todos del layout sin destruirlos
        for pw in self._plots:
            self._layout.removeWidget(pw)
            pw.setVisible(False)
        # Re-añadir en el orden deseado
        for idx in node_indices:
            if 0 <= idx < len(self._plots):
                self._layout.addWidget(self._plots[idx], stretch=1)
                self._plots[idx].setVisible(True)

    def update_plots(
        self,
        raw_bufs:  list[Optional[np.ndarray]],
        filt_bufs: list[Optional[np.ndarray]],
        node_labels: list[str],
    ) -> None:
        """
        Refresh all visible plots with the latest buffer data.

        Args:
            raw_bufs:    List indexed by node (0-3). None or empty → leave unchanged.
            filt_bufs:   Same length as raw_bufs. None → hide filt curve.
            node_labels: Title for each channel, e.g. ["Maestro", "Esclavo 1", ...].
        """
        for i, (pw, rc, fc) in enumerate(
            zip(self._plots, self._raw_curves, self._filt_curves)
        ):
            if not pw.isVisible():
                continue

            raw = raw_bufs[i] if i < len(raw_bufs) else None
            filt = filt_bufs[i] if i < len(filt_bufs) else None

            if raw is not None and len(raw) > 0:
                tail = raw[-self._disp_samp :]
                x    = np.arange(len(tail), dtype=np.float32) / config.FS
                rc.setData(x, tail.astype(np.float32))
            else:
                rc.setData([], [])

            if filt is not None and len(filt) > 0:
                tail = filt[-self._disp_samp :]
                x    = np.arange(len(tail), dtype=np.float32) / config.FS
                fc.setData(x, tail.astype(np.float32))
                fc.setVisible(True)
            else:
                fc.setVisible(False)

            # Update title if label provided
            if i < len(node_labels):
                pw.setTitle(node_labels[i], color="w", size="10pt")

    def clear_all(self) -> None:
        """Clear every curve to a blank state."""
        for rc, fc in zip(self._raw_curves, self._filt_curves):
            rc.setData([], [])
            fc.setData([], [])
        for pw in self._plots:
            pw.plotItem.enableAutoRange(x=True, y=True)

    def clear_node(self, node_idx: int) -> None:
        """Clear the curves for one node immediately."""
        if 0 <= node_idx < len(self._raw_curves):
            self._raw_curves[node_idx].setData([], [])
            self._filt_curves[node_idx].setData([], [])
            self._filt_curves[node_idx].setVisible(False)
            self._plots[node_idx].plotItem.enableAutoRange(x=True, y=True)

    # ── Private ───────────────────────────────────────────────────────────────

    def set_theme(self, dark: bool) -> None:
        """Switch plots between dark (default) and light theme."""
        bg  = "k"       if dark else "w"
        fg  = "#e0e0e0" if dark else "#202020"
        raw_pen  = self.RAW_PEN        if dark else self.RAW_PEN_LIGHT
        filt_pen = self.FILT_PEN       if dark else self.FILT_PEN_LIGHT
        for i, pw in enumerate(self._plots):
            pw.setBackground(bg)
            for ax_name in ("left", "bottom"):
                ax = pw.getAxis(ax_name)
                ax.setPen(pg.mkPen(fg))
                ax.setTextPen(pg.mkPen(fg))
            pw.setTitle(config.NODE_NAMES[i], color=fg, size="10pt")
        for rc in self._raw_curves:
            rc.setPen(raw_pen)
        for fc in self._filt_curves:
            fc.setPen(filt_pen)

    def _build_plots(self) -> None:
        """Create MAX_NODES PlotWidgets and add them to the layout."""
        for i in range(config.MAX_NODES):
            pw = pg.PlotWidget(title=config.NODE_NAMES[i])
            pw.setLabel("left",   "Voltage", units="V")
            pw.setLabel("bottom", "Time", units="s")
            pw.showGrid(x=True, y=True, alpha=0.3)
            pw.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            pw.setMinimumHeight(80)

            rc = pw.plot(pen=self.RAW_PEN,  name="Raw")
            fc = pw.plot(pen=self.FILT_PEN, name="Filtered")
            fc.setVisible(False)

            # Legend
            pw.addLegend(offset=(5, 5))
            pw.plotItem.legend.addItem(rc, "Raw")       # type: ignore[union-attr]
            pw.plotItem.legend.addItem(fc, "Filtered")  # type: ignore[union-attr]

            self._plots.append(pw)
            self._raw_curves.append(rc)
            self._filt_curves.append(fc)

            self._layout.addWidget(pw, stretch=1)

        # Initially hide the master slot (index 0); master is gateway only
        self._plots[0].setVisible(False)

        # Acoplar solo el eje X entre plots de esclavos (Y independiente por ganancias)
        ref = self._plots[1]
        for pw in self._plots[2:]:
            pw.setXLink(ref)
