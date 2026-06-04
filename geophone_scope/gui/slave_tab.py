"""gui/slave_tab.py — Per-slave PGA, VDAC, FIR, notch and debug controls."""

from __future__ import annotations

from typing import Optional

import serial.tools.list_ports
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,   # usado por notch spinboxes
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config
from debug_port import DebugPortWorker


def _list_ports() -> list[str]:
    ports = sorted(p.device for p in serial.tools.list_ports.comports())
    return ports if ports else ["(no ports)"]


class SlaveTab(QWidget):
    """
    Controls for one slave node (channels 1-3 internally, displayed as 1-N).

    ch_index: 1-based slave index matching protocol node_id.
    """

    # ── Signals ───────────────────────────────────────────────────────────────
    alias_changed:    pyqtSignal = pyqtSignal(int, str)     # ch_index, alias
    vdac_changed:     pyqtSignal = pyqtSignal(int, int)     # ch_index, vdac_byte
    pgavdac_changed:  pyqtSignal = pyqtSignal(int, int)     # ch_index, pga_code
    pga_changed:      pyqtSignal = pyqtSignal(int, int)     # ch_index, pga_code
    fir_apply:        pyqtSignal = pyqtSignal(int, str)     # ch_index, cmd_str
    fir_remove:       pyqtSignal = pyqtSignal(int)          # ch_index
    dc_remove_toggled: pyqtSignal = pyqtSignal(int, bool)  # ch_index, enabled
    test_requested:   pyqtSignal = pyqtSignal(int)          # ch_index
    ver_requested:    pyqtSignal = pyqtSignal(int)          # ch_index
    send_all_requested: pyqtSignal = pyqtSignal(int)        # ch_index
    notch_toggled:    pyqtSignal = pyqtSignal(int, bool)    # ch_index, enabled
    notch_mu_changed: pyqtSignal = pyqtSignal(int, float)  # ch_index, mu
    notch_harm_changed: pyqtSignal = pyqtSignal(int, int)  # ch_index, n_harm
    latency_requested: pyqtSignal = pyqtSignal(int)        # ch_index

    def __init__(self, ch_index: int, parent: Optional[QWidget] = None) -> None:
        """
        Args:
            ch_index: 1-based slave index (1=Slave1, 2=Slave2, 3=Slave3).
        """
        super().__init__(parent)
        self.ch_index = ch_index
        self._pgavdac_code = 0
        self._vdac_byte    = 128
        self._prev_pga_code = 0
        self._gain_target_v: dict[int, float] = {}   # pga_code → último Target V
        self._fir_active   = False
        self._vref_values = self._build_vref_values()
        self._debug_worker: Optional[DebugPortWorker] = None
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vbox  = QVBoxLayout(inner)
        vbox.setContentsMargins(2, 2, 2, 2)
        vbox.setSpacing(6)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # ── Identificación ───────────────────────────────────────────────────
        grp_id = QGroupBox("Identificación")
        form_id = QFormLayout(grp_id)

        self.dd_type = QComboBox()
        self.dd_type.addItems(["Hammer", "Geo1", "Geo2"])
        _defaults = {1: "Hammer", 2: "Geo1", 3: "Geo2"}
        _di = self.dd_type.findText(_defaults.get(self.ch_index, "Geo1"))
        if _di >= 0:
            self.dd_type.setCurrentIndex(_di)
        self.dd_type.currentTextChanged.connect(
            lambda txt: self.alias_changed.emit(self.ch_index, txt)
        )
        form_id.addRow("Tipo:", self.dd_type)

        self.lbl_mac = QLabel("—")
        self.lbl_mac.setStyleSheet("font-family: monospace; font-size: 9px; color: #888;")
        form_id.addRow("MAC:", self.lbl_mac)
        vbox.addWidget(grp_id)

        # ── VDAC group ────────────────────────────────────────────────────────
        grp_vdac = QGroupBox("VRef DC (VDAC)")
        form_v = QFormLayout(grp_vdac)

        row_target = QHBoxLayout()
        self.ef_target_v = QDoubleSpinBox()
        self.ef_target_v.setRange(
            0.0,
            config.VDAC_FULL_SCALE_V * max(config.GAIN_CODES),
        )
        self.ef_target_v.setDecimals(4)
        self.ef_target_v.setSingleStep(config.VDAC_STEP)
        self.ef_target_v.setValue(0.512)
        self.ef_target_v.setSuffix(" V")
        self.ef_target_v.editingFinished.connect(self._on_target_v_changed)
        btn_minus = QPushButton("−")
        btn_plus  = QPushButton("+")
        btn_minus.setFixedWidth(28)
        btn_plus.setFixedWidth(28)
        btn_minus.clicked.connect(lambda: self._adjust_vref(-1))
        btn_plus.clicked.connect(lambda:  self._adjust_vref(+1))
        row_target.addWidget(self.ef_target_v, 1)
        row_target.addWidget(btn_minus)
        row_target.addWidget(btn_plus)
        form_v.addRow("Target V:", row_target)

        row_pgavdac = QHBoxLayout()
        self.lbl_pgavdac = QLabel("PGAvdac: 1x (auto)")
        self.lbl_vdac_dot = QLabel("●")
        self.lbl_vdac_dot.setStyleSheet("color: grey; font-size: 14px;")
        self.lbl_vdac_dot.setToolTip("VDAC sin confirmar")
        row_pgavdac.addWidget(self.lbl_pgavdac, 1)
        row_pgavdac.addWidget(self.lbl_vdac_dot)
        form_v.addRow(row_pgavdac)
        vbox.addWidget(grp_vdac)

        # ── PGA group ─────────────────────────────────────────────────────────
        grp_pga = QGroupBox("PGA Gain")
        hbox_pga = QHBoxLayout(grp_pga)
        self.dd_pga = QComboBox()
        self.dd_pga.addItems(config.GAIN_NAMES)
        self.dd_pga.currentIndexChanged.connect(self._on_pga_changed)
        self.lbl_pga_lock_dot = QLabel("●")
        self.lbl_pga_lock_dot.setToolTip("PGA lock")
        self.lbl_pga_actual = QLabel("Current: 1x")
        hbox_pga.addWidget(self.dd_pga)
        hbox_pga.addWidget(self.lbl_pga_lock_dot)
        hbox_pga.addWidget(self.lbl_pga_actual)
        self.set_pga_lock(0)
        vbox.addWidget(grp_pga)

        # ── FIR filter group ──────────────────────────────────────────────────
        grp_fir = QGroupBox("FIR Filter")
        vbox_fir = QVBoxLayout(grp_fir)

        row_fir_cmd = QHBoxLayout()
        row_fir_cmd.addWidget(QLabel("Cmd:"))
        self.ef_fir = QLineEdit()
        self.ef_fir.setPlaceholderText("firls(73,(0,1,2,3,4,5),(0,0,1,1,0,0),fs=FS)")
        self.ef_fir.setToolTip(
            "FIR command: lp/hp/bp/bs, firls(...), remez(...), firwin(...), firwin2(...), or b=[...]"
        )
        row_fir_cmd.addWidget(self.ef_fir, 1)
        self.btn_apply_fir = QPushButton("Apply")
        self.btn_apply_fir.clicked.connect(self._on_fir_toggle)
        row_fir_cmd.addWidget(self.btn_apply_fir)
        vbox_fir.addLayout(row_fir_cmd)

        row_fir_dc = QHBoxLayout()
        self.cb_dc_remove = QCheckBox("Remove DC")
        self.cb_dc_remove.toggled.connect(
            lambda v: self.dc_remove_toggled.emit(self.ch_index, v)
        )
        self.lbl_dc_val = QLabel("DC: --")
        self.lbl_dc_val.setStyleSheet("font-size: 9px; color: #888;")
        self.lbl_fir_status = QLabel("No filter")
        self.lbl_fir_status.setStyleSheet("font-size: 9px;")
        row_fir_dc.addWidget(self.cb_dc_remove)
        row_fir_dc.addWidget(self.lbl_dc_val)
        row_fir_dc.addWidget(self.lbl_fir_status, 1)
        vbox_fir.addLayout(row_fir_dc)
        vbox.addWidget(grp_fir)

        # ── Test / Ver group ──────────────────────────────────────────────────
        grp_test = QGroupBox("Test")
        vbox_test = QVBoxLayout(grp_test)

        row_test = QHBoxLayout()
        self.btn_test = QPushButton(f"Test Slave {self.ch_index}")
        self.btn_test.setEnabled(False)
        self.btn_test.clicked.connect(lambda: self.test_requested.emit(self.ch_index))
        self.spn_test_secs = QDoubleSpinBox()
        self.spn_test_secs.setRange(0.5, 10.0)
        self.spn_test_secs.setValue(1.5)
        self.spn_test_secs.setSingleStep(0.5)
        self.spn_test_secs.setDecimals(1)
        self.spn_test_secs.setSuffix(" s")
        self.spn_test_secs.setFixedWidth(68)
        self.spn_test_secs.setToolTip("Duración del test")
        self.lbl_health_dot = QLabel("●")
        self.lbl_health_dot.setStyleSheet("color: grey; font-size: 16px;")
        self.lbl_health_txt = QLabel("No test")
        row_test.addWidget(self.btn_test)
        row_test.addWidget(self.spn_test_secs)
        row_test.addWidget(self.lbl_health_dot)
        row_test.addWidget(self.lbl_health_txt, 1)
        vbox_test.addLayout(row_test)

        row_ver = QHBoxLayout()
        btn_ver = QPushButton("Ver")
        btn_ver.setToolTip("Single capture for live VDAC calibration")
        btn_ver.clicked.connect(lambda: self.ver_requested.emit(self.ch_index))
        btn_latency = QPushButton("Probe latency")
        btn_latency.clicked.connect(lambda: self.latency_requested.emit(self.ch_index))
        row_ver.addWidget(btn_ver)
        row_ver.addWidget(btn_latency)
        vbox_test.addLayout(row_ver)

        btn_send_all = QPushButton("⬆ Enviar configuración")
        btn_send_all.setToolTip("Envía PGAvdac, VDAC y PGA de una sola vez")
        btn_send_all.clicked.connect(lambda: self.send_all_requested.emit(self.ch_index))
        vbox_test.addWidget(btn_send_all)
        vbox.addWidget(grp_test)

        # ── Stats group ───────────────────────────────────────────────────────
        grp_stats = QGroupBox("Statistics")
        vbox_stats = QVBoxLayout(grp_stats)
        self.lbl_stats     = QLabel("Batches: 0  Samples: 0")
        self.lbl_last_val  = QLabel("Last: --")
        self.lbl_drift     = QLabel("Drift: --")
        self.lbl_latency   = QLabel("Latency: --")
        self.lbl_psoc      = QLabel("PSoC: ?")
        for lbl in (self.lbl_stats, self.lbl_last_val, self.lbl_drift,
                    self.lbl_latency, self.lbl_psoc):
            lbl.setStyleSheet("font-size: 9px;")
            vbox_stats.addWidget(lbl)
        vbox.addWidget(grp_stats)

        # ── Notch 50 Hz group ─────────────────────────────────────────────────
        grp_notch = QGroupBox("50 Hz Notch Canceller (LMS)")
        vbox_notch = QVBoxLayout(grp_notch)

        self.cb_notch = QCheckBox("Enable")
        self.cb_notch.toggled.connect(
            lambda v: self.notch_toggled.emit(self.ch_index, v)
        )
        vbox_notch.addWidget(self.cb_notch)

        row_notch_params = QHBoxLayout()
        row_notch_params.addWidget(QLabel("µ:"))
        self.spn_notch_mu = QDoubleSpinBox()
        self.spn_notch_mu.setRange(1e-6, 1.0)
        self.spn_notch_mu.setDecimals(5)
        self.spn_notch_mu.setSingleStep(0.0005)
        self.spn_notch_mu.setValue(config.NOTCH_DEFAULT_MU)
        self.spn_notch_mu.valueChanged.connect(
            lambda v: self.notch_mu_changed.emit(self.ch_index, v)
        )
        row_notch_params.addWidget(self.spn_notch_mu)

        row_notch_params.addWidget(QLabel("Harmonics:"))
        self.spn_notch_harm = QSpinBox()
        self.spn_notch_harm.setRange(1, 5)
        self.spn_notch_harm.setValue(config.NOTCH_DEFAULT_HARM)
        self.spn_notch_harm.valueChanged.connect(
            lambda v: self.notch_harm_changed.emit(self.ch_index, v)
        )
        row_notch_params.addWidget(self.spn_notch_harm)
        vbox_notch.addLayout(row_notch_params)
        vbox.addWidget(grp_notch)

        # ── Debug COM group ───────────────────────────────────────────────────
        grp_dbg = QGroupBox(f"Slave {self.ch_index} logs COM (optional)")
        vbox_dbg = QVBoxLayout(grp_dbg)

        row_dbg_port = QHBoxLayout()
        self.dd_dbg_port = QComboBox()
        self.dd_dbg_port.addItems(_list_ports())
        btn_dbg_refresh = QPushButton("↺")
        btn_dbg_refresh.setFixedWidth(30)
        btn_dbg_refresh.clicked.connect(self._refresh_dbg_ports)
        row_dbg_port.addWidget(self.dd_dbg_port, 1)
        row_dbg_port.addWidget(btn_dbg_refresh)
        vbox_dbg.addLayout(row_dbg_port)

        row_dbg_btns = QHBoxLayout()
        self.btn_dbg_connect    = QPushButton("Open log")
        self.btn_dbg_disconnect = QPushButton("Close log")
        self.btn_dbg_disconnect.setEnabled(False)
        self.btn_dbg_connect.clicked.connect(self._on_dbg_connect)
        self.btn_dbg_disconnect.clicked.connect(self._on_dbg_disconnect)
        row_dbg_btns.addWidget(self.btn_dbg_connect)
        row_dbg_btns.addWidget(self.btn_dbg_disconnect)
        vbox_dbg.addLayout(row_dbg_btns)

        self.ta_dbg = QTextEdit()
        self.ta_dbg.setReadOnly(True)
        self.ta_dbg.setFontFamily("Courier New")
        self.ta_dbg.setFontPointSize(8)
        self.ta_dbg.setMaximumHeight(200)
        vbox_dbg.addWidget(self.ta_dbg)
        vbox.addWidget(grp_dbg)

        vbox.addStretch(1)

    # ── Internal event handlers ───────────────────────────────────────────────

    @staticmethod
    def _vref_output_v(vdac_byte: int, pga_code: int) -> float:
        gain = config.GAIN_CODES[pga_code]
        return float(vdac_byte) * config.VDAC_STEP * gain

    # Ganancia máxima del PGAvdac — valores más altos añaden demasiado ruido
    _PGAVDAC_MAX_GAIN = 8

    @staticmethod
    def _build_vref_values() -> list[tuple[float, int, int]]:
        values: list[tuple[float, int, int]] = []
        for pga_code, gain in enumerate(config.GAIN_CODES):
            if gain > SlaveTab._PGAVDAC_MAX_GAIN:
                continue
            for byte in range(config.VDAC_MIN, config.VDAC_MAX + 1):
                values.append((byte * config.VDAC_STEP * gain, byte, pga_code))
        values.sort(key=lambda item: (item[0], config.GAIN_CODES[item[2]], item[1]))
        return values

    def _set_vref_widgets(self, vdac_byte: int, pga_code: int) -> None:
        self._pgavdac_code = pga_code
        self._vdac_byte    = vdac_byte
        target_v = self._vref_output_v(vdac_byte, pga_code)

        self.ef_target_v.blockSignals(True)
        self.ef_target_v.setValue(target_v)
        self.ef_target_v.blockSignals(False)

        step_v = config.VDAC_STEP * config.GAIN_CODES[pga_code]
        self.lbl_pgavdac.setText(
            f"PGAvdac: {config.GAIN_NAMES[pga_code]} (auto, step {step_v:.3f} V)"
        )

    def _apply_vref_setting(self, vdac_byte: int, pga_code: int) -> None:
        old_byte = self._vdac_byte
        old_pga  = self._pgavdac_code
        self._set_vref_widgets(vdac_byte, pga_code)
        if pga_code != old_pga:
            self.pgavdac_changed.emit(self.ch_index, pga_code)
        if vdac_byte != old_byte:
            self.vdac_changed.emit(self.ch_index, vdac_byte)

    def _adjust_vref(self, delta: int) -> None:
        current = self._vref_output_v(self._vdac_byte, self._pgavdac_code)
        eps = 1e-12
        if delta > 0:
            candidates = [item for item in self._vref_values if item[0] > current + eps]
            target = candidates[0] if candidates else self._vref_values[-1]
        else:
            candidates = [item for item in self._vref_values if item[0] < current - eps]
            target = candidates[-1] if candidates else self._vref_values[0]
        _, byte, pcode = target
        self._apply_vref_setting(byte, pcode)

    def _on_target_v_changed(self) -> None:
        target = self.ef_target_v.value()
        byte, pcode = self._calc_vdac(target)
        self._apply_vref_setting(byte, pcode)

    @staticmethod
    def _calc_vdac(target_v: float) -> tuple[int, int]:
        """Return the closest representable (vdac_byte, pgavdac_code) within gain limit."""
        target_v = max(0.0, target_v)
        best: tuple[float, float, int, int] | None = None
        for pcode, gain in enumerate(config.GAIN_CODES):
            if gain > SlaveTab._PGAVDAC_MAX_GAIN:
                continue
            byte = round(target_v / (gain * config.VDAC_STEP))
            byte = min(config.VDAC_MAX, max(config.VDAC_MIN, byte))
            actual = byte * config.VDAC_STEP * gain
            err = abs(actual - target_v)
            step = gain * config.VDAC_STEP
            candidate = (err, step, pcode, byte)
            if best is None or candidate < best:
                best = candidate
        assert best is not None
        _, _, pcode, byte = best
        return byte, pcode

    def _on_pga_changed(self, index: int) -> None:
        # Guardar Target V del gain anterior y restaurar el del nuevo
        self._gain_target_v[self._prev_pga_code] = self.ef_target_v.value()
        if index in self._gain_target_v:
            byte, pcode = self._calc_vdac(self._gain_target_v[index])
            self._apply_vref_setting(byte, pcode)
        self._prev_pga_code = index
        self.lbl_pga_actual.setText(f"Current: {config.GAIN_NAMES[index]}")
        self.pga_changed.emit(self.ch_index, index)

    def _on_fir_toggle(self) -> None:
        if self._fir_active:
            self.fir_remove.emit(self.ch_index)
        else:
            cmd = self.ef_fir.text().strip()
            self.fir_apply.emit(self.ch_index, cmd)

    def _refresh_dbg_ports(self) -> None:
        current = self.dd_dbg_port.currentText()
        self.dd_dbg_port.clear()
        ports = _list_ports()
        self.dd_dbg_port.addItems(ports)
        if current in ports:
            self.dd_dbg_port.setCurrentText(current)

    def _on_dbg_connect(self) -> None:
        port = self.dd_dbg_port.currentText()
        if not port or port == "(no ports)":
            return
        if self._debug_worker is not None:
            self._on_dbg_disconnect()
        self._debug_worker = DebugPortWorker(port)
        self._debug_worker.line_received.connect(self._on_dbg_line)
        self._debug_worker.connection_changed.connect(self._on_dbg_conn_changed)
        self._debug_worker.start()

    def _on_dbg_disconnect(self) -> None:
        if self._debug_worker is not None:
            self._debug_worker.stop()
            self._debug_worker.wait(2000)
            self._debug_worker = None
        self.btn_dbg_connect.setEnabled(True)
        self.btn_dbg_disconnect.setEnabled(False)

    def _on_dbg_line(self, line: str) -> None:
        self.ta_dbg.append(line)
        # Keep at most 500 lines
        doc = self.ta_dbg.document()
        if doc.blockCount() > 500:
            cursor = self.ta_dbg.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def _on_dbg_conn_changed(self, connected: bool) -> None:
        self.btn_dbg_connect.setEnabled(not connected)
        self.btn_dbg_disconnect.setEnabled(connected)

    # ── Slots called by main_window ──────────────────────────────────────────

    def set_alias(self, alias: str) -> None:
        """Select the matching type in the dropdown without triggering the signal."""
        self.dd_type.blockSignals(True)
        idx = self.dd_type.findText(alias)
        if idx >= 0:
            self.dd_type.setCurrentIndex(idx)
        self.dd_type.blockSignals(False)

    def set_connected(self, connected: bool) -> None:
        """Enable/disable test button based on master connection."""
        self.btn_test.setEnabled(connected)
        if not connected:
            self.set_pga_lock(0)

    def update_stats(
        self,
        batch_count: int,
        total_samples: int,
        last_val: Optional[float],
        drift_str: str,
        latency_str: str,
        psoc_ok: Optional[bool],
    ) -> None:
        self.lbl_stats.setText(f"Batches: {batch_count}  Samples: {total_samples}")
        self.lbl_last_val.setText(f"Last: {last_val:.6f} V" if last_val is not None else "Last: --")
        self.lbl_drift.setText(f"Drift: {drift_str}")
        self.lbl_latency.setText(f"Latency: {latency_str}")
        if psoc_ok is None:
            self.lbl_psoc.setText("PSoC: ?")
        elif psoc_ok:
            self.lbl_psoc.setText("PSoC: DETECTED")
        else:
            self.lbl_psoc.setText("PSoC: not detected")

    def set_vdac_lock(self, state: int) -> None:
        """state: 0=unknown, 1=ok, 2=pending."""
        if state == 1:
            self.lbl_vdac_dot.setStyleSheet("color: #21a33a; font-size: 14px;")
            self.lbl_vdac_dot.setToolTip("VDAC confirmado por PSoC")
        elif state == 2:
            self.lbl_vdac_dot.setStyleSheet("color: #d71920; font-size: 14px;")
            self.lbl_vdac_dot.setToolTip("VDAC pendiente")
        else:
            self.lbl_vdac_dot.setStyleSheet("color: grey; font-size: 14px;")
            self.lbl_vdac_dot.setToolTip("VDAC sin confirmar")

    def set_dc_value(self, v: Optional[float]) -> None:
        if v is None:
            self.lbl_dc_val.setText("DC: --")
        else:
            self.lbl_dc_val.setText(f"DC: {v * 1000:.1f} mV")

    def set_fir_status(self, status: str) -> None:
        self.lbl_fir_status.setText(status)
        self._fir_active = (status != "No filter")
        self.btn_apply_fir.setText("Remove" if self._fir_active else "Apply")

    def set_health(self, health: int) -> None:
        """health: 0=unknown, 1=ok, 2=fail."""
        if health == 1:
            self.lbl_health_dot.setStyleSheet("color: green; font-size: 16px;")
            self.lbl_health_txt.setText("OK")
        elif health == 2:
            self.lbl_health_dot.setStyleSheet("color: red; font-size: 16px;")
            self.lbl_health_txt.setText("FAIL")
        else:
            self.lbl_health_dot.setStyleSheet("color: grey; font-size: 16px;")
            self.lbl_health_txt.setText("No test")

    @property
    def vdac_byte(self) -> int:
        return self._vdac_byte

    def get_gain_target_v(self) -> dict[int, float]:
        return dict(self._gain_target_v)

    def set_gain_target_v(self, mapping: dict[int, float]) -> None:
        self._gain_target_v.update(mapping)

    def set_vdac(self, byte_val: int) -> None:
        """Update VDAC without re-emitting (to avoid feedback loop)."""
        self._set_vref_widgets(byte_val, self._pgavdac_code)

    def set_pgavdac(self, code: int) -> None:
        """Update PGAvdac state without re-emitting."""
        if 0 <= code < len(config.GAIN_CODES):
            self._set_vref_widgets(self._vdac_byte, code)

    def set_pga(self, code: int) -> None:
        """Update PGA dropdown without re-emitting."""
        self.dd_pga.blockSignals(True)
        self.dd_pga.setCurrentIndex(code)
        self.lbl_pga_actual.setText(f"Current: {config.GAIN_NAMES[code]}")
        self.dd_pga.blockSignals(False)

    def set_pga_lock(self, state: int) -> None:
        """state: 0=unknown, 1=locked, 2=pending/fail."""
        if state == 1:
            self.lbl_pga_lock_dot.setStyleSheet("color: #21a33a; font-size: 16px;")
            self.lbl_pga_lock_dot.setToolTip("PGA confirmado por PSoC")
        elif state == 2:
            self.lbl_pga_lock_dot.setStyleSheet("color: #d71920; font-size: 16px;")
            self.lbl_pga_lock_dot.setToolTip("PGA pendiente o sin confirmacion")
        else:
            self.lbl_pga_lock_dot.setStyleSheet("color: grey; font-size: 16px;")
            self.lbl_pga_lock_dot.setToolTip("PGA sin confirmar")

    def set_mac(self, mac: str) -> None:
        """Display the slave's MAC address (auto-read, not user-editable)."""
        self.lbl_mac.setText(mac.upper())
        self.lbl_mac.setStyleSheet("font-family: monospace; font-size: 9px; color: #4CAF50;")
