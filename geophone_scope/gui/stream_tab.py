"""gui/stream_tab.py — Connection, ARM, START/STOP and save controls tab."""

from __future__ import annotations

import math
from typing import Optional

import serial.tools.list_ports
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import config


def _list_serial_ports() -> list[str]:
    """Return sorted list of available serial port names."""
    ports = sorted(p.device for p in serial.tools.list_ports.comports())
    return ports if ports else ["(no ports)"]


class StreamTab(QWidget):
    """
    Left-panel tab with connection, sync, stream, and save controls.

    Emits signals that main_window connects to application logic.
    """

    # ── Signals ───────────────────────────────────────────────────────────────
    connect_requested:     pyqtSignal = pyqtSignal(str, int)   # port, baud
    disconnect_requested:  pyqtSignal = pyqtSignal()
    arm_requested:         pyqtSignal = pyqtSignal(int)         # n_slaves
    start_requested:       pyqtSignal = pyqtSignal(int)         # n_batches
    stop_requested:        pyqtSignal = pyqtSignal()
    save_requested:        pyqtSignal = pyqtSignal(str)         # base filename
    clear_requested:       pyqtSignal = pyqtSignal()
    n_slaves_changed:      pyqtSignal = pyqtSignal(int)
    debug_toggled:         pyqtSignal = pyqtSignal(bool)
    multi_start_toggled:   pyqtSignal = pyqtSignal(bool)
    channel_vis_changed:   pyqtSignal = pyqtSignal(int, bool)  # node_idx, visible
    disp_secs_changed:     pyqtSignal = pyqtSignal(int)
    max_buf_secs_changed:  pyqtSignal = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._connected = False
        self._streaming = False
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # Wrap everything in a scroll area so it doesn't clip on small screens
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vbox  = QVBoxLayout(inner)
        vbox.setContentsMargins(2, 2, 2, 2)
        vbox.setSpacing(6)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # ── Connection group ──────────────────────────────────────────────────
        grp_conn = QGroupBox("Master link")
        form_conn = QFormLayout(grp_conn)

        row_port = QHBoxLayout()
        self.dd_port = QComboBox()
        self.dd_port.addItems(_list_serial_ports())
        self.dd_port.setMinimumWidth(140)
        btn_refresh = QPushButton("↺")
        btn_refresh.setFixedWidth(30)
        btn_refresh.setToolTip("Refresh port list")
        btn_refresh.clicked.connect(self._refresh_ports)
        row_port.addWidget(self.dd_port, 1)
        row_port.addWidget(btn_refresh)
        form_conn.addRow("Master COM:", row_port)

        row_slaves = QHBoxLayout()
        self.spn_slaves = QSpinBox()
        self.spn_slaves.setRange(0, config.MAX_NODES - 1)
        self.spn_slaves.setValue(0)
        self.spn_slaves.valueChanged.connect(
            lambda v: self.n_slaves_changed.emit(v)
        )
        row_slaves.addWidget(self.spn_slaves)
        row_slaves.addStretch()
        form_conn.addRow("Slaves (0=all):", row_slaves)

        row_btns = QHBoxLayout()
        self.btn_connect    = QPushButton("Connect")
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.setEnabled(False)
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        row_btns.addWidget(self.btn_connect)
        row_btns.addWidget(self.btn_disconnect)
        form_conn.addRow(row_btns)

        self.lbl_conn = QLabel("● Not connected")
        self.lbl_conn.setStyleSheet("color: grey;")
        form_conn.addRow(self.lbl_conn)
        vbox.addWidget(grp_conn)

        # ── Sync group ────────────────────────────────────────────────────────
        grp_sync = QGroupBox("Synchronisation")
        vbox_sync = QVBoxLayout(grp_sync)

        self.btn_arm = QPushButton("Discover (ARM)")
        self.btn_arm.setEnabled(False)
        self.btn_arm.clicked.connect(self._on_arm)
        vbox_sync.addWidget(self.btn_arm)

        self.lbl_sync_state = QLabel("State: IDLE")
        self.lbl_ready_cnt  = QLabel("Slaves ready: 0")
        vbox_sync.addWidget(self.lbl_sync_state)
        vbox_sync.addWidget(self.lbl_ready_cnt)
        vbox.addWidget(grp_sync)

        # ── Stream group ──────────────────────────────────────────────────────
        grp_stream = QGroupBox("Stream")
        vbox_stream = QVBoxLayout(grp_stream)

        self.btn_start_stop = QPushButton("▶ Start")
        self.btn_start_stop.setEnabled(False)
        self.btn_start_stop.clicked.connect(self._on_start_stop)
        vbox_stream.addWidget(self.btn_start_stop)

        row_batches = QHBoxLayout()
        row_batches.addWidget(QLabel("Batches:"))
        self.spn_batches = QSpinBox()
        self.spn_batches.setRange(1, 65535)
        self.spn_batches.setValue(80)
        self.spn_batches.valueChanged.connect(self._update_batch_info)
        row_batches.addWidget(self.spn_batches)
        row_batches.addStretch()
        vbox_stream.addLayout(row_batches)

        self.lbl_batch_info = QLabel("80 bat → 2400 smp ≈ 2.4 s")
        self.lbl_batch_info.setStyleSheet("font-size: 9px;")
        vbox_stream.addWidget(self.lbl_batch_info)

        self.lbl_mem_warn = QLabel("")
        vbox_stream.addWidget(self.lbl_mem_warn)
        self.spn_batches.valueChanged.connect(self._update_mem_warn)

        row_chk = QHBoxLayout()
        self.cb_debug = QCheckBox("Debug")
        self.cb_debug.toggled.connect(self.debug_toggled.emit)
        self.cb_multi = QCheckBox("Multiple starts")
        self.cb_multi.toggled.connect(self.multi_start_toggled.emit)
        row_chk.addWidget(self.cb_debug)
        row_chk.addWidget(self.cb_multi)
        vbox_stream.addLayout(row_chk)

        btn_clear = QPushButton("Clear buffers")
        btn_clear.clicked.connect(self.clear_requested.emit)
        vbox_stream.addWidget(btn_clear)
        vbox.addWidget(grp_stream)

        # ── Latency / ESP-NOW reaction ────────────────────────────────────────
        grp_lat = QGroupBox("ESP-NOW Reaction")
        vbox_lat = QVBoxLayout(grp_lat)
        self.lbl_latency: list[QLabel] = []
        for k in range(1, 4):
            lbl = QLabel(f"S{k}: --")
            lbl.setStyleSheet("font-size: 9px;")
            self.lbl_latency.append(lbl)
            vbox_lat.addWidget(lbl)
        vbox.addWidget(grp_lat)

        # ── Data counters ─────────────────────────────────────────────────────
        grp_data = QGroupBox("Data received")
        vbox_data = QVBoxLayout(grp_data)
        self.lbl_node_data: list[QLabel] = []
        for i in range(1, config.MAX_NODES):  # slaves only
            lbl = QLabel(f"{config.NODE_NAMES[i]}: 0 bat (0 smp)")
            lbl.setStyleSheet("font-size: 9px;")
            self.lbl_node_data.append(lbl)
            vbox_data.addWidget(lbl)
        vbox.addWidget(grp_data)

        # ── Save group ────────────────────────────────────────────────────────
        grp_save = QGroupBox("Save")
        vbox_save = QVBoxLayout(grp_save)

        row_save = QHBoxLayout()
        row_save.addWidget(QLabel("Name:"))
        self.ef_save_name = QLineEdit(config.DEFAULT_SAVE_NAME)
        row_save.addWidget(self.ef_save_name, 1)
        vbox_save.addLayout(row_save)

        btn_save = QPushButton("Save .mat")
        btn_save.clicked.connect(self._on_save)
        vbox_save.addWidget(btn_save)

        self.lbl_save_status = QLabel("")
        self.lbl_save_status.setStyleSheet("color: green; font-size: 9px;")
        vbox_save.addWidget(self.lbl_save_status)
        vbox.addWidget(grp_save)

        # ── Channel visibility ────────────────────────────────────────────────
        grp_vis = QGroupBox("Visible channels")
        vbox_vis = QVBoxLayout(grp_vis)
        self.cb_visible: list[QCheckBox] = []
        for i in range(1, config.MAX_NODES):
            cb = QCheckBox(config.NODE_NAMES[i])
            cb.setChecked(True)
            cb.setEnabled(False)
            node_i = i  # capture for closure
            cb.toggled.connect(lambda checked, ni=node_i: self.channel_vis_changed.emit(ni, checked))
            self.cb_visible.append(cb)
            vbox_vis.addWidget(cb)
        vbox.addWidget(grp_vis)

        # ── Display settings ──────────────────────────────────────────────────
        grp_disp = QGroupBox("Display")
        form_disp = QFormLayout(grp_disp)

        self.spn_disp_secs = QSpinBox()
        self.spn_disp_secs.setRange(1, self._capture_secs_for_batches(65535))
        self.spn_disp_secs.setValue(3)
        self.spn_disp_secs.setSuffix(" s")
        self.spn_disp_secs.valueChanged.connect(self.disp_secs_changed.emit)
        form_disp.addRow("Window:", self.spn_disp_secs)

        self.spn_max_buf_secs = QSpinBox()
        self.spn_max_buf_secs.setRange(1, self._capture_secs_for_batches(65535))
        self.spn_max_buf_secs.setSingleStep(1)
        self.spn_max_buf_secs.setValue(config.MAX_BUF_S)
        self.spn_max_buf_secs.setSuffix(" s")
        self.spn_max_buf_secs.valueChanged.connect(self.max_buf_secs_changed.emit)
        form_disp.addRow("Max buf:", self.spn_max_buf_secs)
        vbox.addWidget(grp_disp)

        vbox.addStretch(1)
        self._update_batch_info(self.spn_batches.value())
        self._update_mem_warn(self.spn_batches.value())
        self.update_channel_visibility(self.spn_slaves.value())

    # ── Internal handlers ─────────────────────────────────────────────────────

    def _refresh_ports(self) -> None:
        current = self.dd_port.currentText()
        self.dd_port.clear()
        ports = _list_serial_ports()
        self.dd_port.addItems(ports)
        # Restore previous selection if still available
        if current in ports:
            self.dd_port.setCurrentText(current)

    def _on_connect(self) -> None:
        port = self.dd_port.currentText()
        if not port or port == "(no ports)":
            return
        self.connect_requested.emit(port, config.BAUD)

    def _on_disconnect(self) -> None:
        self.disconnect_requested.emit()

    def _on_arm(self) -> None:
        n = self.spn_slaves.value() or (config.MAX_NODES - 1)
        self.arm_requested.emit(n)

    def _on_start_stop(self) -> None:
        if self._streaming:
            self.stop_requested.emit()
        else:
            n = self.spn_batches.value()
            self.start_requested.emit(n)

    def _on_save(self) -> None:
        name = self.ef_save_name.text().strip() or config.DEFAULT_SAVE_NAME
        self.save_requested.emit(name)

    def _update_batch_info(self, n: int) -> None:
        smp = n * config.SAMPLES_PER_BATCH
        dur = smp / config.FS
        self.lbl_batch_info.setText(f"{n} bat → {smp} smp ≈ {dur:.1f} s")
        if hasattr(self, "spn_disp_secs") and hasattr(self, "spn_max_buf_secs"):
            self._sync_display_limits(n)

    def _update_mem_warn(self, n: int) -> None:
        # SampleBytes = 10 bytes + 4 bytes timestamp per batch; ESP32 heap ~80 KB
        store_kb = (n * config.SAMPLES_PER_BATCH * 10 + n * 4) // 1024
        if store_kb > 80:
            self.lbl_mem_warn.setText(
                f'<span style="color:orange;font-size:9px">'
                f'⚠ {store_kb} KB/esclavo — puede fallar (límite ~80 KB)'
                f'</span>'
            )
        else:
            self.lbl_mem_warn.setText("")

    @staticmethod
    def _capture_secs_for_batches(n_batches: int) -> int:
        smp = max(1, int(n_batches)) * config.SAMPLES_PER_BATCH
        return max(1, math.ceil(smp / config.FS))

    def _sync_display_limits(self, n_batches: int) -> None:
        capture_secs = self._capture_secs_for_batches(n_batches)
        self.spn_disp_secs.setRange(1, capture_secs)
        self.spn_max_buf_secs.setRange(1, capture_secs)
        if self.spn_disp_secs.value() < capture_secs:
            self.spn_disp_secs.setValue(capture_secs)
        if self.spn_max_buf_secs.value() < self.spn_disp_secs.value():
            self.spn_max_buf_secs.setValue(self.spn_disp_secs.value())

    # ── Slots called by main_window ──────────────────────────────────────────

    def set_connected(self, connected: bool, port: str = "") -> None:
        """Update connection UI state."""
        self._connected = connected
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        self.btn_arm.setEnabled(connected)
        self.btn_start_stop.setEnabled(connected)
        if connected:
            self.lbl_conn.setText(f"● Connected: {port}")
            self.lbl_conn.setStyleSheet("color: green;")
        else:
            self.lbl_conn.setText("● Not connected")
            self.lbl_conn.setStyleSheet("color: grey;")
            self.set_streaming(False)

    def set_streaming(self, streaming: bool) -> None:
        """Update start/stop button label."""
        self._streaming = streaming
        self.btn_start_stop.setText("■ Stop" if streaming else "▶ Start")

    def set_sync_state(self, text: str) -> None:
        self.lbl_sync_state.setText(f"State: {text}")

    def set_ready_count(self, n: int) -> None:
        self.lbl_ready_cnt.setText(f"Slaves ready: {n}")

    def set_latency_text(self, slave_idx: int, text: str) -> None:
        """slave_idx is 1-based (matching slave node numbering)."""
        if 1 <= slave_idx <= len(self.lbl_latency):
            self.lbl_latency[slave_idx - 1].setText(f"S{slave_idx}: {text}")

    def set_node_data_text(self, slave_idx: int, text: str) -> None:
        """slave_idx is 1-based."""
        if 1 <= slave_idx <= len(self.lbl_node_data):
            self.lbl_node_data[slave_idx - 1].setText(text)

    def set_save_status(self, msg: str, ok: bool = True) -> None:
        color = "green" if ok else "red"
        self.lbl_save_status.setStyleSheet(f"color: {color}; font-size: 9px;")
        self.lbl_save_status.setText(msg)

    def update_channel_visibility(self, n_slaves: int) -> None:
        """Enable/check slave checkboxes for active slaves, disable others."""
        for node_idx, cb in enumerate(self.cb_visible, start=1):
            is_active = node_idx <= n_slaves
            cb.setEnabled(is_active)
            cb.setChecked(is_active)

    def set_channel_visible(self, node_idx: int, visible: bool, *, emit: bool = False) -> None:
        """Set a slave visibility checkbox by protocol node index."""
        if not 1 <= node_idx <= len(self.cb_visible):
            return
        cb = self.cb_visible[node_idx - 1]
        cb.blockSignals(not emit)
        cb.setChecked(visible)
        cb.blockSignals(False)

    def is_channel_visible(self, node_idx: int) -> bool:
        """Return slave visibility checkbox state by protocol node index."""
        if not 1 <= node_idx <= len(self.cb_visible):
            return False
        return self.cb_visible[node_idx - 1].isChecked()
