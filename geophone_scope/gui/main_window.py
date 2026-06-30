"""gui/main_window.py — QMainWindow: wires all components together."""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional

import numpy as np
from PyQt6.QtCore import QSettings, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

_LIGHT_STYLESHEET = """
QWidget          { background: #f0f0f0; color: #1a1a1a; }
QGroupBox        { border: 1px solid #bbb; border-radius: 3px; margin-top: 6px;
                   font-weight: bold; color: #1a1a1a; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 2px; }
QPushButton      { background: #e0e0e0; border: 1px solid #aaa; border-radius: 3px;
                   padding: 3px 8px; color: #1a1a1a; }
QPushButton:hover    { background: #d0d0d0; }
QPushButton:pressed  { background: #c0c0c0; }
QPushButton:disabled { background: #ddd; color: #888; }
QSpinBox, QComboBox, QLineEdit { background: #fff; border: 1px solid #aaa; color: #1a1a1a; }
QCheckBox  { color: #1a1a1a; }
QLabel     { color: #1a1a1a; }
QTextEdit  { background: #fff; color: #1a1a1a; }
QTabWidget::pane  { border: 1px solid #bbb; }
QTabBar::tab      { background: #e8e8e8; color: #333; padding: 4px 8px;
                    border: 1px solid #bbb; border-bottom: none; }
QTabBar::tab:selected { background: #f5f5f5; color: #000; }
QScrollArea { border: none; background: #f0f0f0; }
"""
from scipy.io import savemat

import config
import signal_proc
from data_store import DataStore, NodeData
from logger import Logger
from protocol import (
    Packet,
    encode_directed,
    encode_std,
    encode_std16,
)
from serial_worker import SerialWorker

from gui.plot_area import PlotArea
from gui.slave_tab import SlaveTab
from gui.stream_tab import StreamTab


class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(
        self,
        log_dir: str,
        data_dir: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Geophone Multi-Node Scope")
        self.resize(1440, 920)

        # ── State ─────────────────────────────────────────────────────────────
        self._data       = DataStore()
        self._n_slaves   = 0
        self._connected  = False
        self._streaming  = False
        self._worker: Optional[SerialWorker] = None
        self._data_dir   = data_dir
        self._log_dir    = log_dir
        self._discover_expected = 0
        self._drift_probe_t0: dict[int, float] = {}
        self._mac_partial: dict[int, dict[int, int]] = {}   # node → {sub→bytes}
        self._test_timers:  dict[int, QTimer]        = {}   # ch_index → timer activo
        self._loading_settings = False
        self._dark_mode  = True
        self._settings = QSettings("Tesis", "Geophone Scope")

        # ── Logger ────────────────────────────────────────────────────────────
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._logger = Logger(log_dir, stamp, callback=None, max_sessions=config.N_LOG_SESSIONS)

        # ── Build UI ──────────────────────────────────────────────────────────
        self._build_ui()

        # Connect logger callback after ta_log widget exists
        self._logger.set_callback(self._append_log_tab)
        self._load_settings()

        # ── Render timer (main thread) ────────────────────────────────────────
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(config.RENDER_PERIOD_MS)
        self._render_timer.timeout.connect(self._render)
        self._render_timer.start()

        self._logger.log_human("Application started")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        h_layout = QHBoxLayout(central)
        h_layout.setContentsMargins(4, 4, 4, 4)
        h_layout.setSpacing(4)

        # ── Left panel (tabs) ─────────────────────────────────────────────────
        left_widget = QWidget()
        left_widget.setFixedWidth(config.LEFT_PANEL_W)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._btn_theme = QPushButton("☀ Claro")
        self._btn_theme.setFixedHeight(22)
        self._btn_theme.setToolTip("Alternar modo claro / oscuro")
        self._btn_theme.clicked.connect(self._toggle_theme)
        left_layout.addWidget(self._btn_theme)

        self._tab_widget = QTabWidget()
        left_layout.addWidget(self._tab_widget)

        # Stream tab (always first)
        self._stream_tab = StreamTab()
        self._tab_widget.addTab(self._stream_tab, "Stream")

        # Slave tabs (one per MAX_NODES-1 possible slave)
        self._slave_tabs: list[SlaveTab] = []
        for i in range(1, config.MAX_NODES):
            st = SlaveTab(ch_index=i)
            self._slave_tabs.append(st)
            self._tab_widget.addTab(st, config.NODE_NAMES[i])

        # Log tab (always last)
        self._ta_log = QTextEdit()
        self._ta_log.setReadOnly(True)
        self._ta_log.setFontFamily("Courier New")
        self._ta_log.setFontPointSize(8)
        self._tab_widget.addTab(self._ta_log, "Log")

        h_layout.addWidget(left_widget)

        # ── Right panel (plots) ───────────────────────────────────────────────
        self._plot_area = PlotArea()
        h_layout.addWidget(self._plot_area, 1)

        # ── Wire StreamTab signals ────────────────────────────────────────────
        st = self._stream_tab
        st.connect_requested.connect(self._on_connect)
        st.disconnect_requested.connect(self._on_disconnect)
        st.arm_requested.connect(self._on_arm)
        st.start_requested.connect(self._on_start)
        st.stop_requested.connect(self._on_stop)
        st.save_requested.connect(self._on_save)
        st.clear_requested.connect(self._on_clear)
        st.n_slaves_changed.connect(self._on_n_slaves_changed)
        st.debug_toggled.connect(self._on_debug_toggled)
        st.multi_start_toggled.connect(self._on_multi_start_toggled)
        st.channel_vis_changed.connect(self._on_channel_vis_changed)
        st.disp_secs_changed.connect(self._on_disp_secs_changed)
        st.max_buf_secs_changed.connect(self._on_max_buf_secs_changed)
        st.dd_port.currentTextChanged.connect(self._save_settings)
        st.spn_secs.valueChanged.connect(self._save_settings)
        st.spn_secs.valueChanged.connect(self._on_capture_secs_changed)
        st.ef_save_name.textChanged.connect(self._save_settings)

        # ── Wire SlaveTab signals ─────────────────────────────────────────────
        for slave_tab in self._slave_tabs:
            slave_tab.alias_changed.connect(self._on_alias_changed)
            slave_tab.vdac_changed.connect(self._on_vdac_changed)
            slave_tab.pgavdac_changed.connect(self._on_pgavdac_changed)
            slave_tab.pga_changed.connect(self._on_pga_changed)
            slave_tab.fir_apply.connect(self._on_fir_apply)
            slave_tab.fir_remove.connect(self._on_fir_remove)
            slave_tab.dc_remove_toggled.connect(self._on_dc_remove_toggled)
            slave_tab.test_requested.connect(self._on_test)
            slave_tab.ver_requested.connect(self._on_ver)
            slave_tab.notch_toggled.connect(self._on_notch_toggled)
            slave_tab.notch_harm_changed.connect(self._on_notch_harm)
            slave_tab.latency_requested.connect(self._on_latency_probe)
            slave_tab.dd_dbg_port.currentTextChanged.connect(self._save_settings)
            slave_tab.ef_fir.textChanged.connect(self._save_settings)
            slave_tab.spn_test_secs.valueChanged.connect(self._on_capture_secs_changed)
            slave_tab.send_all_requested.connect(self._on_send_all)

        # Initial plot visibility
        self._update_plot_visibility()

    # ── Serial connection ─────────────────────────────────────────────────────

    @pyqtSlot(str, int)
    def _on_connect(self, port: str, baud: int) -> None:
        if self._worker is not None:
            self._on_disconnect()

        self._worker = SerialWorker(port, baud)
        self._worker.packet_received.connect(self._on_packet)
        self._worker.connection_changed.connect(self._on_connection_changed)
        self._worker.log_message.connect(self._on_worker_log)
        self._worker.start()

        self._logger.log_human(f"Connecting to {port}")
        self._logger.log_machine(f"onConnect port={port} baud={baud}")

    @pyqtSlot()
    def _on_disconnect(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(3000)
            self._worker = None
        self._connected = False
        self._streaming = False
        self._stream_tab.set_connected(False)
        for st in self._slave_tabs:
            st.set_connected(False)
        self._logger.log_human("Disconnected")
        self._logger.log_machine("onDisconnect")

    @pyqtSlot(bool)
    def _on_connection_changed(self, connected: bool) -> None:
        self._connected = connected
        port = self._worker.port_name if self._worker else ""
        self._stream_tab.set_connected(connected, port)
        for st in self._slave_tabs:
            st.set_connected(connected)
        if connected and self._stream_tab.cb_debug.isChecked():
            self._cmd(config.CMD_DEBUG, 1)
        if not connected:
            self._streaming = False
            self._stream_tab.set_streaming(False)

    @pyqtSlot(str)
    def _on_worker_log(self, msg: str) -> None:
        self._logger.log_machine(msg)

    # ── Packet reception (main thread via signal) ─────────────────────────────

    @pyqtSlot(object)
    def _on_packet(self, pkt: Packet) -> None:
        """Dispatch every incoming packet to the appropriate handler."""
        try:
            if pkt.is_data:
                self._handle_data(pkt)
            elif pkt.is_heartbeat:
                self._handle_heartbeat(pkt)
            elif pkt.is_ack:
                self._handle_ack(pkt)
            elif pkt.is_ready:
                self._handle_ready(pkt)
            elif pkt.is_latency:
                self._handle_latency(pkt)
            elif pkt.is_status:
                self._handle_status(pkt)
        except Exception as exc:
            self._logger.log_machine(f"on_packet error: {exc}")

    def _handle_data(self, pkt: Packet) -> None:
        """ADC sample — append to node buffer and apply signal processing."""
        node_idx = pkt.node_id   # protocol node_id maps directly to NodeData index
        if node_idx < 0 or node_idx >= config.MAX_NODES:
            return
        nd: NodeData = self._data[node_idx]
        raw_val = float(pkt.value24) / config.ADC_COUNTS_PER_VOLT

        # Track first-sample timing for drift measurement
        if not nd.got_first:
            nd.got_first = True
            nd.t_first   = time.monotonic()
            t0 = self._drift_probe_t0.pop(node_idx, None)
            if t0 is not None:
                nd.add_drift(nd.t_first - t0)

        nd.raw_buf.append(raw_val)
        nd.total_samples += 1

        # Batch accounting (every SAMPLES_PER_BATCH samples = one batch)
        if nd.total_samples % config.SAMPLES_PER_BATCH == 0:
            nd.batch_count += 1

        # FIR filter + DC removal — streamed sample-by-sample.
        # The harmonic notch needs the *complete* capture to fit f0 and its
        # harmonics, so it can't run incrementally: while it's enabled,
        # _render() rebuilds filt_buf from scratch each tick (see
        # _reprocess_filt_buf) and the live per-sample path is skipped here.
        if not nd.notch_enabled:
            if nd.filt_b is not None:
                arr = np.array([raw_val], dtype=np.float64)
                y, nd.filt_zi = signal_proc.fir_filter(nd.filt_b, arr, nd.filt_zi)
                filt_val = float(y[0])
            else:
                filt_val = raw_val

            if nd.dc_remove and len(nd.raw_buf) > 1:
                filt_val -= sum(nd.raw_buf) / len(nd.raw_buf)

            nd.filt_buf.append(filt_val)

    def _handle_heartbeat(self, pkt: Packet) -> None:
        """Update node PGA, VDAC, master state from heartbeat."""
        node_idx = pkt.node_id
        if node_idx >= config.MAX_NODES:
            return
        nd: NodeData = self._data[node_idx]
        nd.pga_code  = pkt.hb_pga
        nd.vdac_byte = pkt.hb_vdac
        master_state = pkt.hb_master_state
        state_name   = config.MASTER_STATE_NAMES.get(master_state, str(master_state))

        # Update sync state label only for master (node_id 0 or 0xFF range)
        if node_idx == 0:
            self._stream_tab.set_sync_state(state_name)
            if self._streaming and master_state in (
                config.MASTER_STATE_IDLE,
                config.MASTER_STATE_ARMED,
            ):
                self._streaming = False
                self._stream_tab.set_streaming(False)

        self._logger.log_machine(
            f"HB node={node_idx} pga={nd.pga_code} vdac={nd.vdac_byte} state={state_name}"
        )

        # Mirror to slave tab
        if 1 <= node_idx <= len(self._slave_tabs):
            self._slave_tabs[node_idx - 1].set_pga(nd.pga_code)
            self._slave_tabs[node_idx - 1].set_vdac(nd.vdac_byte)

    def _handle_ack(self, pkt: Packet) -> None:
        """ACK from master — clear pending for this (node, sub_cmd)."""
        node_idx = pkt.node_id
        ack_cmd  = pkt.ack_cmd
        ack_val  = pkt.ack_val
        pending: Optional[tuple[int, float, int]] = None
        if node_idx < config.MAX_NODES:
            nd = self._data[node_idx]
            pending = nd.pending.pop(ack_cmd, None)
        if ack_cmd == config.SUBCMD_PGA and 1 <= node_idx <= len(self._slave_tabs):
            if ack_val:
                self._slave_tabs[node_idx - 1].set_pga_lock(1)
                gain = config.GAIN_NAMES[self._data[node_idx].pga_code]
                self._logger.log_human(f"S{node_idx} PGA confirmado: {gain}")
            else:
                self._slave_tabs[node_idx - 1].set_pga_lock(2)
                expected = pending[0] if pending else self._data[node_idx].pga_code
                gain = config.GAIN_NAMES[expected] if 0 <= expected < len(config.GAIN_NAMES) else str(expected)
                self._logger.log_human(f"S{node_idx} PGA sin lock: {gain}")
        if ack_cmd == config.SUBCMD_VDAC and 1 <= node_idx <= len(self._slave_tabs):
            self._slave_tabs[node_idx - 1].set_vdac_lock(1 if ack_val else 2)
        self._logger.log_machine(
            f"ACK node={node_idx} cmd={ack_cmd:#04x} val={ack_val}"
        )

    def _check_pga_lock_timeout(self, ch_index: int, expected_code: int) -> None:
        """Mark PGA as failed if the pending command did not ACK in time."""
        if not (1 <= ch_index < config.MAX_NODES):
            return
        pending = self._data[ch_index].pending.get(config.SUBCMD_PGA)
        if pending is None or pending[0] != expected_code:
            return
        self._data[ch_index].pending.pop(config.SUBCMD_PGA, None)
        self._slave_tabs[ch_index - 1].set_pga_lock(2)
        gain = config.GAIN_NAMES[expected_code]
        self._logger.log_human(f"S{ch_index} PGA sin confirmación: {gain}")

    def _handle_ready(self, pkt: Packet) -> None:
        """READY packet — update slaves-ready counter."""
        n = pkt.ready_n_slaves
        self._stream_tab.set_ready_count(n)
        if self._discover_expected:
            self._set_active_slave_count(n)
            if n >= self._discover_expected:
                self._stream_tab.set_sync_state("ARMED")
                self._discover_expected = 0
        self._logger.log_human(f"READY: {n} slaves ready")
        self._logger.log_machine(f"READY n={n}")

    def _handle_latency(self, pkt: Packet) -> None:
        """START latency packet — record for the originating slave."""
        node_idx = pkt.node_id
        tof_us   = pkt.value24u
        if 0 <= node_idx < config.MAX_NODES:
            nd = self._data[node_idx]
            nd.add_latency(float(tof_us))
            lat_str = nd.format_latency_stats()
            self._stream_tab.set_latency_text(node_idx, lat_str)
            if 1 <= node_idx <= len(self._slave_tabs):
                self._slave_tabs[node_idx - 1].update_stats(
                    nd.batch_count,
                    nd.total_samples,
                    float(nd.raw_buf[-1]) if nd.raw_buf else None,
                    nd.format_drift_stats(),
                    lat_str,
                    nd.psoc_ok,
                )
        self._logger.log_machine(f"LATENCY node={node_idx} tof={tof_us}us")

    def _handle_status(self, pkt: Packet) -> None:
        """Status (master) or HELLO (slave)."""
        if pkt.node_id == 0xFF:
            espnow_ok = pkt.status_espnow_ok
            ap_ch     = pkt.status_ap_ch
            self._logger.log_human(
                f"Master status: espnow_ok={espnow_ok} ch={ap_ch}"
            )
        elif pkt.b2 in (0x02, 0x03, 0x04):
            # MAC byte-pair packet (sent after HELLO)
            node_idx = pkt.node_id
            if 1 <= node_idx <= len(self._slave_tabs):
                parts = self._mac_partial.setdefault(node_idx, {})
                parts[pkt.b2] = (pkt.hello_mac_hi, pkt.hello_mac_lo)
                if 0x02 in parts and 0x03 in parts and 0x04 in parts:
                    mac_bytes = (
                        parts[0x02][0], parts[0x02][1],
                        parts[0x03][0], parts[0x03][1],
                        parts[0x04][0], parts[0x04][1],
                    )
                    mac_str = ":".join(f"{b:02X}" for b in mac_bytes)
                    self._data[node_idx].mac = mac_str
                    self._slave_tabs[node_idx - 1].set_mac(mac_str)
                    self._settings.setValue(f"mac/node{node_idx}", mac_str)
                    self._mac_partial.pop(node_idx, None)
                    self._logger.log_human(f"HELLO slave={node_idx} MAC={mac_str}")
        elif pkt.b2 == 0x05:
            # Exact Fs sub-packet: b1:b0 is uint16 Hz. Legacy HELLO still
            # arrives for older clients, but only this packet can carry 2929 Hz.
            node_idx = pkt.node_id
            fs_hz = pkt.hello_fs_exact_hz
            if 0 <= node_idx < config.MAX_NODES and fs_hz > 0:
                for slave_nd in self._data.nodes[1:]:
                    slave_nd.fs = float(fs_hz)
                    slave_nd.fs_known = True
                self._stream_tab.set_fs(self._effective_fs())
                self._refresh_fs_dependent_windows()
            self._logger.log_human(f"HELLO slave={node_idx} fs_exact={fs_hz}Hz")
        elif pkt.b2 == 0x01:
            # Slave HELLO (legacy b0=fs/100)
            node_idx = pkt.node_id
            psoc_ok  = pkt.hello_psoc_ok
            fs_hz    = pkt.hello_fs_hz
            if 0 <= node_idx < config.MAX_NODES:
                nd = self._data[node_idx]
                nd.psoc_ok = psoc_ok
                # El PSoC reporta su fs real (depende de cómo esté programado el
                # front-end analógico) — confiar en eso, no en config.FS estático.
                if fs_hz > 0 and (not nd.fs_known or nd.fs != float(fs_hz)):
                    for slave_nd in self._data.nodes[1:]:
                        slave_nd.fs = float(fs_hz)
                        slave_nd.fs_known = True
                    self._stream_tab.set_fs(self._effective_fs())
                    self._refresh_fs_dependent_windows()
            if 1 <= node_idx <= len(self._slave_tabs):
                nd = self._data[node_idx]
                self._slave_tabs[node_idx - 1].update_stats(
                    nd.batch_count,
                    nd.total_samples,
                    None,
                    nd.format_drift_stats(),
                    nd.format_latency_stats(),
                    psoc_ok,
                )
            fs_str = f" fs={fs_hz}Hz" if fs_hz > 0 else ""
            self._logger.log_human(
                f"HELLO slave={node_idx} psoc_ok={psoc_ok}{fs_str}"
            )
        else:
            self._logger.log_human(
                f"STATUS node={pkt.node_id:#04x} b=[{pkt.b2},{pkt.b1},{pkt.b0}]"
            )
        self._logger.log_machine(
            f"STATUS node={pkt.node_id:#04x} b=[{pkt.b2},{pkt.b1},{pkt.b0}]"
        )

    def _effective_fs(self) -> float:
        """Sample rate to use for batch/duration/display/notch/export calculations.

        The ADC rate depends on how the analog front-end / PSoC is programmed
        and can be reconfigured at any time, so there is NO nominal/guessed
        fallback: this returns the value reported by the first slave that has
        sent a HELLO, or 0.0 if none has yet — callers must treat 0 as
        "unknown" and wait, never substitute a constant.
        """
        for nd in self._data.nodes[1:]:
            if nd.fs_known:
                return nd.fs
        return 0.0

    def _refresh_fs_dependent_windows(self) -> None:
        """Keep sample-count windows aligned when HELLO reports the real fs."""
        fs = self._effective_fs()
        if not fs:
            return
        self._plot_area.set_display_samples(
            int(round(self._stream_tab.spn_disp_secs.value() * fs))
        )
        self._data.resize_all(
            int(round(self._stream_tab.spn_max_buf_secs.value() * fs))
        )

    # ── Render timer callback (main thread) ───────────────────────────────────

    def _render(self) -> None:
        """Called every RENDER_PERIOD_MS — update plots and stats labels."""
        raw_arrays:  list[Optional[np.ndarray]] = []
        filt_arrays: list[Optional[np.ndarray]] = []
        filt_trims:  list[float]                 = []
        labels:      list[str]                   = []

        for i, nd in enumerate(self._data.nodes):
            # The harmonic notch fits over the *complete* capture, so while
            # streaming with it enabled, keep refitting it to the growing
            # buffer each tick (cheap: a single small lstsq solve).
            if nd.notch_enabled and self._streaming:
                self._reprocess_filt_buf(i)

            if nd.raw_buf:
                raw_arrays.append(np.array(nd.raw_buf, dtype=np.float32))
            else:
                raw_arrays.append(None)

            if nd.filt_buf:
                filt_arrays.append(np.array(nd.filt_buf, dtype=np.float32))
            else:
                filt_arrays.append(None)

            # Linear-phase FIR of length N has constant group delay (N-1)/2:
            # its first (N-1)/2 outputs are pure startup transient. Discard
            # them at plot time — the rest aligns with raw at the same x
            # positions, so the transient isn't mistaken for a real signal.
            filt_trims.append((len(nd.filt_b) - 1) / 2.0 if nd.filt_b is not None else 0.0)

            labels.append(config.NODE_NAMES[i])

        self._plot_area.update_plots(raw_arrays, filt_arrays, labels, self._effective_fs(), filt_trims)

        # Update stats labels in slave tabs
        for i, nd in enumerate(self._data.nodes[1:], start=1):
            if i > len(self._slave_tabs):
                break
            last_val = float(nd.raw_buf[-1]) if nd.raw_buf else None
            self._slave_tabs[i - 1].update_stats(
                nd.batch_count,
                nd.total_samples,
                last_val,
                nd.format_drift_stats(),
                nd.format_latency_stats(),
                nd.psoc_ok,
            )
            dc_v = float(np.mean(nd.raw_buf)) if len(nd.raw_buf) > 10 else None
            self._slave_tabs[i - 1].set_dc_value(dc_v)
            self._stream_tab.set_node_data_text(
                i,
                f"{config.NODE_NAMES[i]}: {nd.batch_count} bat ({nd.total_samples} smp)",
            )

    # ── Command helpers ───────────────────────────────────────────────────────

    def _send(self, data: bytes) -> None:
        if self._worker is not None:
            self._worker.send(data)

    def _cmd(self, cmd: int, param: int) -> None:
        self._send(encode_std(cmd, param))

    def _cmd16(self, cmd: int, value: int) -> None:
        self._send(encode_std16(cmd, value))

    def _directed(self, node_idx: int, sub_cmd: int, param: int) -> None:
        self._send(encode_directed(node_idx, sub_cmd, param))

    # ── Stream / sync callbacks ───────────────────────────────────────────────

    def _on_capture_secs_changed(self, secs: float) -> None:
        """Sincroniza el valor de segundos entre el stream tab y todos los slave tabs."""
        for w in [self._stream_tab.spn_secs] + [st.spn_test_secs for st in self._slave_tabs]:
            if abs(w.value() - secs) > 1e-6:
                w.blockSignals(True)
                w.setValue(self._clamp_f(secs, w.minimum(), w.maximum()))
                w.blockSignals(False)
        self._save_settings()

    @pyqtSlot(int)
    def _on_arm(self, n_slaves: int) -> None:
        if not self._connected:
            return
        if n_slaves <= 0:
            n_slaves = config.MAX_NODES - 1
        self._discover_expected = n_slaves
        self._cmd(config.CMD_ARM, n_slaves)
        self._stream_tab.set_sync_state("DISCOVERING...")
        self._logger.log_human(f"ARM → discovering {n_slaves} slaves")
        self._logger.log_machine(f"CMD ARM n={n_slaves}")
        QTimer.singleShot(5000, self._end_discovery_window)

    @pyqtSlot(int)
    def _on_start(self, n_batches: int) -> None:
        if not self._connected:
            return
        fs = self._effective_fs()
        if not fs:
            self._logger.log_human("START cancelado: esperando Fs real del esclavo (HELLO no recibido aun)")
            return
        self._data.clear_all()
        self._plot_area.clear_all()
        for nd in self._data.nodes:
            nd.got_first = False
        self._cmd(config.CMD_STREAM, 1)
        self._cmd16(config.CMD_START, n_batches)
        self._streaming = True
        self._stream_tab.set_streaming(True)
        self._stream_tab.set_sync_state("HOT_WAIT → START")
        capture_ms = int(round(n_batches * config.SAMPLES_PER_BATCH * 1000 / fs))
        self._logger.log_human(f"START store n_batches={n_batches} (~{capture_ms / 1000:.2f} s)")
        self._logger.log_machine(f"CMD START n={n_batches}")

    @pyqtSlot()
    def _on_stop(self) -> None:
        if not self._connected:
            return
        self._cmd(config.CMD_STOP, 0)
        self._cmd(config.CMD_STREAM, 0)
        self._streaming = False
        self._stream_tab.set_streaming(False)
        self._stream_tab.set_sync_state("STOPPING...")
        self._logger.log_human("STOP")
        self._logger.log_machine("CMD STOP")

    @pyqtSlot()
    def _on_clear(self) -> None:
        self._data.clear_all()
        self._plot_area.clear_all()
        self._logger.log_human("Buffers cleared")
        self._logger.log_machine("CLEAR")

    @pyqtSlot(int)
    def _on_n_slaves_changed(self, n: int) -> None:
        self._n_slaves = n
        self._update_slave_tab_visibility(n)
        self._update_plot_visibility()
        self._stream_tab.update_channel_visibility(n)
        self._save_settings()
        self._logger.log_human(f"Slaves: {n}")
        self._logger.log_machine(f"nSlaves={n}")

    def _set_active_slave_count(self, n: int) -> None:
        n = self._clamp(n, 0, config.MAX_NODES - 1)
        if n == self._n_slaves:
            return
        self._stream_tab.spn_slaves.blockSignals(True)
        self._stream_tab.spn_slaves.setValue(n)
        self._stream_tab.spn_slaves.blockSignals(False)
        self._on_n_slaves_changed(n)

    def _end_discovery_window(self) -> None:
        if self._discover_expected and self._connected:
            self._stream_tab.set_sync_state("ARMED" if self._n_slaves > 0 else "IDLE")
        self._discover_expected = 0

    @pyqtSlot(bool)
    def _on_debug_toggled(self, enabled: bool) -> None:
        self._save_settings()
        if self._connected:
            self._cmd(config.CMD_DEBUG, int(enabled))
        self._logger.log_machine(f"debug={int(enabled)}")

    @pyqtSlot(bool)
    def _on_multi_start_toggled(self, enabled: bool) -> None:
        self._save_settings()
        self._logger.log_machine(f"multiStart={int(enabled)}")

    @pyqtSlot(int, bool)
    def _on_channel_vis_changed(self, node_idx: int, visible: bool) -> None:
        self._data[node_idx].visible = visible
        self._update_plot_visibility()
        self._save_settings()

    @pyqtSlot(int)
    def _on_disp_secs_changed(self, secs: int) -> None:
        fs = self._effective_fs()
        if fs:
            self._plot_area.set_display_samples(int(round(secs * fs)))
        self._save_settings()
        self._logger.log_machine(f"dispSecs={secs}")

    @pyqtSlot(int)
    def _on_max_buf_secs_changed(self, secs: int) -> None:
        fs = self._effective_fs()
        if fs:
            self._data.resize_all(int(round(secs * fs)))
        self._save_settings()
        self._logger.log_machine(f"maxBufSecs={secs}")

    # ── Slave control callbacks ───────────────────────────────────────────────

    @pyqtSlot(int, int)
    def _on_vdac_changed(self, ch_index: int, vdac_byte: int) -> None:
        self._data[ch_index].vdac_byte = vdac_byte
        self._save_settings()
        if 1 <= ch_index <= len(self._slave_tabs):
            self._slave_tabs[ch_index - 1].set_vdac_lock(2)  # pendiente
        if not self._connected:
            return
        self._directed(ch_index, config.SUBCMD_VDAC, vdac_byte)
        self._logger.log_human(
            f"S{ch_index} VDAC→{vdac_byte} ({vdac_byte * config.VDAC_STEP:.3f}V)"
        )
        self._logger.log_machine(f"VDAC ch={ch_index} byte={vdac_byte}")

    @pyqtSlot(int, int)
    def _on_pgavdac_changed(self, ch_index: int, pga_code: int) -> None:
        self._data[ch_index].pgavdac = pga_code
        self._save_settings()
        if not self._connected:
            return
        self._directed(ch_index, config.SUBCMD_PGAVDAC, pga_code)
        gain = config.GAIN_NAMES[pga_code]
        self._logger.log_human(f"S{ch_index} PGAvdac→{gain}")
        self._logger.log_machine(f"PGAVDAC ch={ch_index} code={pga_code}")

    @pyqtSlot(int, int)
    def _on_pga_changed(self, ch_index: int, pga_code: int) -> None:
        self._data[ch_index].pga_code = pga_code
        self._save_settings()
        if 1 <= ch_index <= len(self._slave_tabs):
            self._slave_tabs[ch_index - 1].set_pga_lock(2)
        if not self._connected:
            return
        self._data[ch_index].pending[config.SUBCMD_PGA] = (pga_code, time.time(), 0)
        self._directed(ch_index, config.SUBCMD_PGA, pga_code)
        QTimer.singleShot(
            int(config.RETRY_SEC * 1000),
            lambda ch=ch_index, code=pga_code: self._check_pga_lock_timeout(ch, code),
        )
        gain = config.GAIN_NAMES[pga_code]
        self._logger.log_human(f"S{ch_index} PGA→{gain}")
        self._logger.log_machine(f"PGA ch={ch_index} code={pga_code}")

    def _reprocess_filt_buf(self, ch_index: int) -> None:
        """Rebuild filt_buf from raw_buf: FIR -> DC removal -> harmonic notch.

        Used whenever a stage that needs the *complete* capture changes
        (FIR command, DC removal, notch enable/harmonics) — in particular,
        the least-squares harmonic notch only makes sense fitted over the
        whole buffer, so it is always (re)computed here rather than
        sample-by-sample.
        """
        nd = self._data[ch_index]
        nd.filt_buf.clear()
        if not nd.raw_buf:
            nd.filt_zi = None
            return

        raw = np.array(nd.raw_buf, dtype=np.float64)
        if nd.filt_b is not None:
            arr, nd.filt_zi = signal_proc.fir_filter(nd.filt_b, raw, None)
        else:
            arr = raw.copy()
            nd.filt_zi = None

        if nd.dc_remove and len(raw) > 1:
            arr = arr - float(np.mean(raw))

        if nd.notch_enabled:
            arr = signal_proc.harmonic_notch(arr, nd.fs, config.NOTCH_F0, nd.notch_harm)

        nd.filt_buf.extend(arr.astype(np.float32).tolist())

    @pyqtSlot(int, str)
    def _on_fir_apply(self, ch_index: int, cmd_str: str) -> None:
        nd = self._data[ch_index]
        b  = signal_proc.compile_fir_cmd(cmd_str, nd.fs)
        if b is None:
            err = signal_proc.last_fir_error()
            detail = f": {err}" if err else ""
            self._logger.log_human(f"S{ch_index} FIR: invalid command '{cmd_str}'{detail}")
            if ch_index <= len(self._slave_tabs):
                status = f"Invalid: {err}" if err else "Invalid cmd"
                self._slave_tabs[ch_index - 1].set_fir_status(status[:80])
            return
        nd.filt_b   = b
        nd.filt_cmd = cmd_str
        self._reprocess_filt_buf(ch_index)

        self._save_settings()
        n_taps = len(b)
        status = f"{n_taps} taps  {cmd_str}"
        if ch_index <= len(self._slave_tabs):
            self._slave_tabs[ch_index - 1].set_fir_status(status)
        self._logger.log_human(f"S{ch_index} FIR applied: {cmd_str} ({n_taps} taps)")
        self._logger.log_machine(f"FIR ch={ch_index} cmd='{cmd_str}' taps={n_taps}")

    @pyqtSlot(int)
    def _on_fir_remove(self, ch_index: int) -> None:
        nd = self._data[ch_index]
        nd.filt_b   = None
        nd.filt_zi  = None
        nd.filt_cmd = ""
        self._reprocess_filt_buf(ch_index)
        self._save_settings()
        if ch_index <= len(self._slave_tabs):
            self._slave_tabs[ch_index - 1].set_fir_status("No filter")
        self._logger.log_human(f"S{ch_index} FIR removed")
        self._logger.log_machine(f"FIR_REMOVE ch={ch_index}")

    @pyqtSlot(int, bool)
    def _on_dc_remove_toggled(self, ch_index: int, enabled: bool) -> None:
        self._data[ch_index].dc_remove = enabled
        self._save_settings()
        self._logger.log_machine(f"dcRemove ch={ch_index} enabled={int(enabled)}")

    def _prepare_node_capture(self, ch_index: int) -> NodeData:
        """Reset one node and make sure its plot is visible for a manual capture."""
        if ch_index > self._n_slaves:
            self._stream_tab.spn_slaves.blockSignals(True)
            self._stream_tab.spn_slaves.setValue(ch_index)
            self._stream_tab.spn_slaves.blockSignals(False)
            self._on_n_slaves_changed(ch_index)

        nd = self._data[ch_index]
        nd.clear()
        nd.visible = True

        self._stream_tab.set_channel_visible(ch_index, True)

        self._plot_area.clear_node(ch_index)
        self._update_plot_visibility()
        self._save_settings()

        if 1 <= ch_index <= len(self._slave_tabs):
            self._slave_tabs[ch_index - 1].update_stats(
                nd.batch_count,
                nd.total_samples,
                None,
                nd.format_drift_stats(),
                nd.format_latency_stats(),
                nd.psoc_ok,
            )
        self._stream_tab.set_node_data_text(
            ch_index,
            f"{config.NODE_NAMES[ch_index]}: 0 bat (0 smp)",
        )
        return nd

    @pyqtSlot(int)
    def _on_test(self, ch_index: int) -> None:
        if not self._connected:
            return

        # Segunda pulsación mientras el test está corriendo → cancelar y dar por OK
        if ch_index in self._test_timers:
            self._test_timers.pop(ch_index).stop()
            try:
                self._directed(ch_index, config.SUBCMD_DEBUG, 0)
                self._cmd(config.CMD_STREAM, 0)
            except Exception:
                pass
            nd = self._data[ch_index]
            nd.health = 1
            if ch_index <= len(self._slave_tabs):
                self._slave_tabs[ch_index - 1].set_health(1)
            self._logger.log_human(f"Test Slave {ch_index}: OK (cancelado)")
            self._logger.log_machine(f"TEST_CANCEL ch={ch_index}")
            return

        self._logger.log_human(f"Test Slave {ch_index} requested")
        self._logger.log_machine(f"TEST ch={ch_index}")
        nd = self._prepare_node_capture(ch_index)
        nd.health = 0
        self._drift_probe_t0[ch_index] = time.monotonic()
        if ch_index <= len(self._slave_tabs):
            self._slave_tabs[ch_index - 1].set_health(0)
        init_count = nd.batch_count
        self._cmd16(config.CMD_SET_RECLEN, 0)
        self._directed(ch_index, config.SUBCMD_DEBUG, 1)
        self._cmd(config.CMD_STREAM, 1)

        def _check() -> None:
            self._test_timers.pop(ch_index, None)
            gained = nd.batch_count - init_count
            ok = gained >= 2
            nd.health = 1 if ok else 2
            if ch_index <= len(self._slave_tabs):
                self._slave_tabs[ch_index - 1].set_health(nd.health)
            if not ok:
                self._drift_probe_t0.pop(ch_index, None)
                self._logger.log_human(f"Test Slave {ch_index}: FAIL ({gained} batches)")
            else:
                self._logger.log_human(f"Test Slave {ch_index}: OK ({gained} batches)")
            self._logger.log_machine(f"TEST_RESULT ch={ch_index} batches={gained} ok={int(ok)}")
            try:
                self._directed(ch_index, config.SUBCMD_DEBUG, 0)
                self._cmd(config.CMD_STREAM, 0)
            except Exception:
                pass

        test_ms = int(config.TEST_DEFAULT_SECONDS * 1000)
        if ch_index <= len(self._slave_tabs):
            test_ms = int(self._slave_tabs[ch_index - 1].spn_test_secs.value() * 1000)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(_check)
        timer.start(test_ms)
        self._test_timers[ch_index] = timer

    @pyqtSlot(int)
    def _on_ver(self, ch_index: int) -> None:
        if not self._connected:
            return
        fs = self._effective_fs()
        if not fs:
            self._logger.log_human(f"VER Slave {ch_index} cancelado: esperando Fs real del esclavo (HELLO no recibido aun)")
            return
        n = self._stream_tab.batches_value()
        capture_ms = int(round(n * config.SAMPLES_PER_BATCH * 1000 / fs))
        self._prepare_node_capture(ch_index)
        self._cmd16(config.CMD_SET_RECLEN, n)
        self._directed(ch_index, config.SUBCMD_VER, 1)
        self._logger.log_human(f"VER Slave {ch_index} n={n} batches (~{capture_ms / 1000:.2f} s)")
        self._logger.log_machine(f"VER ch={ch_index} n={n}")

    @pyqtSlot(int)
    def _on_send_all(self, ch_index: int) -> None:
        """Envía PGAvdac, VDAC y PGA de una sola vez para configuración rápida."""
        if not self._connected:
            return
        nd = self._data[ch_index]
        st = self._slave_tabs[ch_index - 1] if ch_index <= len(self._slave_tabs) else None
        self._directed(ch_index, config.SUBCMD_PGAVDAC, nd.pgavdac)
        self._directed(ch_index, config.SUBCMD_VDAC,    nd.vdac_byte)
        self._directed(ch_index, config.SUBCMD_PGA,     nd.pga_code)
        if st:
            st.set_pga_lock(2)
            st.set_vdac_lock(2)
        self._logger.log_human(f"S{ch_index} Enviar todo: pgavdac={nd.pgavdac} vdac={nd.vdac_byte} pga={nd.pga_code}")
        self._logger.log_machine(f"SEND_ALL ch={ch_index} pgavdac={nd.pgavdac} vdac={nd.vdac_byte} pga={nd.pga_code}")

    @pyqtSlot(int)
    def _on_latency_probe(self, ch_index: int) -> None:
        if not self._connected:
            return
        nd = self._data[ch_index]
        nd.latency_hist.clear()
        self._stream_tab.set_latency_text(ch_index, "--")
        gap_ms = max(1, int(config.START_LATENCY_PROBE_GAP_S * 1000))
        for k in range(config.START_LATENCY_PROBES):
            QTimer.singleShot(
                k * gap_ms,
                lambda seq=k: self._send_latency_probe_once(ch_index, seq),
            )
        self._logger.log_human(
            f"Latency probe -> Slave {ch_index} ({config.START_LATENCY_PROBES} probes)"
        )
        self._logger.log_machine(
            f"LATENCY_PROBE ch={ch_index} n={config.START_LATENCY_PROBES} gap_ms={gap_ms}"
        )

    def _send_latency_probe_once(self, ch_index: int, seq: int) -> None:
        if not self._connected:
            return
        self._directed(ch_index, config.SUBCMD_LATENCY, seq % 256)

    # ── Notch callbacks ───────────────────────────────────────────────────────

    @pyqtSlot(int, bool)
    def _on_notch_toggled(self, ch_index: int, enabled: bool) -> None:
        nd = self._data[ch_index]
        nd.notch_enabled = enabled
        self._reprocess_filt_buf(ch_index)
        self._save_settings()
        self._logger.log_machine(f"notch ch={ch_index} enabled={int(enabled)}")

    @pyqtSlot(int, int)
    def _on_notch_harm(self, ch_index: int, n_harm: int) -> None:
        nd = self._data[ch_index]
        if nd.notch_harm != n_harm:
            nd.notch_harm = n_harm
            if nd.notch_enabled:
                self._reprocess_filt_buf(ch_index)
        self._save_settings()

    # ── Persistent settings ──────────────────────────────────────────────────

    def _save_settings(self, *_args) -> None:
        """Persist user-facing configuration without saving transient samples."""
        if self._loading_settings:
            return

        s = self._settings
        st = self._stream_tab

        port = st.dd_port.currentText().strip()
        if port and port != "(no ports)":
            s.setValue("stream/usb_port", port)
        s.setValue("stream/n_slaves", st.spn_slaves.value())
        s.setValue("stream/secs", st.spn_secs.value())
        s.setValue("stream/debug_enabled", st.cb_debug.isChecked())
        s.setValue("stream/multi_start", st.cb_multi.isChecked())
        s.setValue("stream/save_name", st.ef_save_name.text().strip())
        s.setValue("stream/display_secs", st.spn_disp_secs.value())
        s.setValue("stream/max_buf_secs", st.spn_max_buf_secs.value())

        for node_idx in range(1, config.MAX_NODES):
            s.setValue(f"visible/node{node_idx}", self._data[node_idx].visible)

        for slave_tab in self._slave_tabs:
            ch = slave_tab.ch_index
            prefix = f"slave/{ch}/"
            dbg_port = slave_tab.dd_dbg_port.currentText().strip()
            if dbg_port and dbg_port != "(no ports)":
                s.setValue(prefix + "debug_port", dbg_port)
            s.setValue(prefix + "fir_cmd", slave_tab.ef_fir.text().strip())
            s.setValue(prefix + "dc_remove", slave_tab.cb_dc_remove.isChecked())
            s.setValue(prefix + "notch_enabled", slave_tab.cb_notch.isChecked())
            s.setValue(prefix + "notch_harm", slave_tab.spn_notch_harm.value())
            s.setValue(prefix + "vdac_byte", slave_tab.vdac_byte)
            for code, val in slave_tab.get_gain_target_v().items():
                s.setValue(prefix + f"gain_target_v/{code}", float(val))
            s.setValue(prefix + "pgavdac_code", slave_tab._pgavdac_code)
            s.setValue(prefix + "pga_code", slave_tab.dd_pga.currentIndex())

        s.sync()

    def _load_settings(self) -> None:
        """Restore persisted GUI settings after the widgets are built."""
        self._loading_settings = True
        try:
            st = self._stream_tab
            self._select_combo_text(
                st.dd_port,
                str(self._settings.value("stream/usb_port", "") or ""),
                add_missing=False,
            )

            n_slaves = self._setting_int("stream/n_slaves", st.spn_slaves.value())
            st.spn_slaves.setValue(self._clamp(n_slaves, st.spn_slaves.minimum(), st.spn_slaves.maximum()))

            cap_secs = self._setting_float("stream/secs", st.spn_secs.value())
            st.spn_secs.setValue(self._clamp_f(cap_secs, st.spn_secs.minimum(), st.spn_secs.maximum()))

            st.cb_debug.setChecked(self._setting_bool("stream/debug_enabled", st.cb_debug.isChecked()))
            st.cb_multi.setChecked(self._setting_bool("stream/multi_start", st.cb_multi.isChecked()))
            st.ef_save_name.setText(
                str(self._settings.value("stream/save_name", st.ef_save_name.text()) or config.DEFAULT_SAVE_NAME)
            )

            disp_secs = self._setting_int("stream/display_secs", st.spn_disp_secs.value())
            max_buf_secs = self._setting_int("stream/max_buf_secs", st.spn_max_buf_secs.value())
            st.spn_disp_secs.setValue(self._clamp(disp_secs, st.spn_disp_secs.minimum(), st.spn_disp_secs.maximum()))
            st.spn_max_buf_secs.setValue(self._clamp(max_buf_secs, st.spn_max_buf_secs.minimum(), st.spn_max_buf_secs.maximum()))
            st._sync_display_limits(st.batches_value())

            for node_idx in range(1, config.MAX_NODES):
                default_visible = node_idx <= self._n_slaves
                visible = (
                    self._setting_bool(f"visible/node{node_idx}", default_visible)
                    and node_idx <= self._n_slaves
                )
                self._data[node_idx].visible = visible
                st.set_channel_visible(node_idx, visible, emit=False)

            for slave_tab in self._slave_tabs:
                ch = slave_tab.ch_index
                prefix = f"slave/{ch}/"
                nd = self._data[ch]

                self._select_combo_text(
                    slave_tab.dd_dbg_port,
                    str(self._settings.value(prefix + "debug_port", "") or ""),
                    add_missing=False,
                )

                fir_cmd = str(self._settings.value(prefix + "fir_cmd", "") or "")
                slave_tab.ef_fir.setText(fir_cmd)
                # No se restaura el filtro al arrancar — siempre arranca sin filtro

                dc_remove = self._setting_bool(prefix + "dc_remove", nd.dc_remove)
                slave_tab.cb_dc_remove.setChecked(dc_remove)
                nd.dc_remove = dc_remove

                notch_harm = self._setting_int(prefix + "notch_harm", nd.notch_harm)
                slave_tab.spn_notch_harm.setValue(
                    self._clamp(notch_harm, slave_tab.spn_notch_harm.minimum(), slave_tab.spn_notch_harm.maximum())
                )
                nd.notch_harm = slave_tab.spn_notch_harm.value()

                notch_enabled = self._setting_bool(prefix + "notch_enabled", nd.notch_enabled)
                slave_tab.cb_notch.setChecked(notch_enabled)
                nd.notch_enabled = notch_enabled

                pgavdac = self._setting_int(prefix + "pgavdac_code", nd.pgavdac)
                pgavdac = self._clamp(pgavdac, 0, len(config.GAIN_CODES) - 1)
                vdac = self._setting_int(prefix + "vdac_byte", nd.vdac_byte)
                vdac = self._clamp(vdac, config.VDAC_MIN, config.VDAC_MAX)
                pga = self._setting_int(prefix + "pga_code", nd.pga_code)
                pga = self._clamp(pga, 0, len(config.GAIN_CODES) - 1)

                slave_tab.set_pgavdac(pgavdac)
                slave_tab.set_vdac(vdac)
                slave_tab.set_pga(pga)
                nd.pgavdac = pgavdac
                nd.vdac_byte = vdac
                nd.pga_code = pga

                # Restaurar memoria Target V por ganancia
                gain_map: dict[int, float] = {}
                for code in range(len(config.GAIN_CODES)):
                    key = prefix + f"gain_target_v/{code}"
                    if self._settings.contains(key):
                        gain_map[code] = self._setting_float(key, 0.0)
                if gain_map:
                    slave_tab.set_gain_target_v(gain_map)
                    slave_tab._prev_pga_code = pga

            self._on_disp_secs_changed(st.spn_disp_secs.value())
            self._on_max_buf_secs_changed(st.spn_max_buf_secs.value())
            self._update_slave_tab_visibility(self._n_slaves)
            self._update_plot_visibility()
            for slave_tab in self._slave_tabs:
                ch = slave_tab.ch_index
                alias = str(self._settings.value(f"alias/node{ch}", "") or "")
                mac   = str(self._settings.value(f"mac/node{ch}",   "") or "")
                if alias:
                    slave_tab.set_alias(alias)
                    self._update_tab_title(ch)
                if mac:
                    self._data[ch].mac = mac
                    slave_tab.set_mac(mac)
            self._dark_mode = self._setting_bool("ui/dark_mode", True)
            self._apply_theme()
        finally:
            self._loading_settings = False

    def _restore_loaded_fir(self, ch_index: int, cmd_str: str) -> None:
        nd = self._data[ch_index]
        if not cmd_str.strip():
            nd.filt_b = None
            nd.filt_zi = None
            nd.filt_cmd = ""
            if ch_index <= len(self._slave_tabs):
                self._slave_tabs[ch_index - 1].set_fir_status("No filter")
            return
        b = signal_proc.compile_fir_cmd(cmd_str, nd.fs)
        if b is None:
            err = signal_proc.last_fir_error()
            if ch_index <= len(self._slave_tabs):
                status = f"Invalid: {err}" if err else "Invalid cmd"
                self._slave_tabs[ch_index - 1].set_fir_status(status[:80])
            return
        nd.filt_b = b
        nd.filt_zi = None
        nd.filt_cmd = cmd_str
        if ch_index <= len(self._slave_tabs):
            self._slave_tabs[ch_index - 1].set_fir_status(f"{len(b)} taps  {cmd_str}")

    @staticmethod
    def _select_combo_text(combo, text: str, *, add_missing: bool = True) -> None:
        text = text.strip()
        if not text:
            return
        idx = combo.findText(text)
        if idx < 0:
            if not add_missing:
                return
            combo.addItem(text)
            idx = combo.findText(text)
        combo.setCurrentIndex(idx)

    @staticmethod
    def _clamp(value: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, int(value)))

    @staticmethod
    def _clamp_f(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(value)))

    def _setting_int(self, key: str, default: int) -> int:
        try:
            return int(self._settings.value(key, default))
        except (TypeError, ValueError):
            return int(default)

    def _setting_float(self, key: str, default: float) -> float:
        try:
            return float(self._settings.value(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    # ── Save ──────────────────────────────────────────────────────────────────

    @pyqtSlot(str)
    def _on_save(self, base_name: str) -> None:
        os.makedirs(self._data_dir, exist_ok=True)
        now = datetime.now()
        stamp = now.strftime("%Y%m%d_%H%M%S")
        clean_base = os.path.splitext(base_name.strip() or config.DEFAULT_SAVE_NAME)[0]
        fname = os.path.join(self._data_dir, f"{clean_base}_{stamp}.mat")
        try:
            save_dict: dict = {
                "schema":       "geophone_scope_mat_node_prefix_v1",
                "source":       "python_desktop_gui",
                "created_at":   now.isoformat(timespec="seconds"),
                "fs":           self._effective_fs(),
                "n_slaves":     self._n_slaves,
                "n_batches":    self._stream_tab.batches_value(),
                "save_time":    stamp,
                "samples_per_batch": config.SAMPLES_PER_BATCH,
                "signal_units": "V",
                "adc_counts_per_volt": config.ADC_COUNTS_PER_VOLT,
                "vdac_step_volts": config.VDAC_STEP,
                "usb_port": self._stream_tab.dd_port.currentText(),
                "display_secs": self._stream_tab.spn_disp_secs.value(),
                "max_buf_secs": self._stream_tab.spn_max_buf_secs.value(),
                "visible_nodes": np.array(
                    [i for i in range(1, config.MAX_NODES) if self._data[i].visible and i <= self._n_slaves],
                    dtype=np.int16,
                ),
            }
            for i, nd in enumerate(self._data.nodes):
                prefix = f"node{i}_"
                save_dict[prefix + "fs"] = nd.fs
                save_dict[prefix + "fs_known"] = bool(nd.fs_known)
                raw_v = np.array(nd.raw_buf, dtype=np.float32)
                filt_v = np.array(nd.filt_buf, dtype=np.float32)
                save_dict[prefix + "raw"]        = raw_v
                save_dict[prefix + "filt"]       = filt_v
                save_dict[prefix + "raw_v"]      = raw_v
                save_dict[prefix + "filt_v"]     = filt_v
                save_dict[prefix + "raw_count"]  = raw_v.size
                save_dict[prefix + "filt_count"] = filt_v.size
                save_dict[prefix + "batch_count"] = nd.batch_count
                save_dict[prefix + "total_samples"] = nd.total_samples
                save_dict[prefix + "pga_code"]   = nd.pga_code
                save_dict[prefix + "vdac_byte"]  = nd.vdac_byte
                save_dict[prefix + "pgavdac_code"] = nd.pgavdac
                save_dict[prefix + "visible"] = bool(nd.visible)
                save_dict[prefix + "fir_cmd"] = nd.filt_cmd
                save_dict[prefix + "dc_remove"] = bool(nd.dc_remove)
                save_dict[prefix + "notch_enabled"] = bool(nd.notch_enabled)
                save_dict[prefix + "notch_harm"] = int(nd.notch_harm)
                save_dict[prefix + "pga_gain"] = (
                    config.GAIN_CODES[nd.pga_code]
                    if 0 <= nd.pga_code < len(config.GAIN_CODES)
                    else np.nan
                )
                save_dict[prefix + "pgavdac_gain"] = (
                    config.GAIN_CODES[nd.pgavdac]
                    if 0 <= nd.pgavdac < len(config.GAIN_CODES)
                    else np.nan
                )
                save_dict[prefix + "slave_id"]   = nd.slave_id
                save_dict[prefix + "name"] = (
                    config.NODE_NAMES[i] if i < len(config.NODE_NAMES) else f"Node {i}"
                )
                save_dict[prefix + "psoc_ok"] = (
                    np.nan if nd.psoc_ok is None else bool(nd.psoc_ok)
                )
                save_dict[prefix + "mac"] = nd.mac
                save_dict[prefix + "health"] = nd.health
                save_dict[prefix + "drift_hist"] = np.array(nd.drift_hist, dtype=np.float64)
                save_dict[prefix + "latency_hist"] = np.array(nd.latency_hist, dtype=np.float64)
            savemat(fname, save_dict, do_compression=True, oned_as="column")
            self._stream_tab.set_save_status(f"Saved: {os.path.basename(fname)}", ok=True)
            self._logger.log_human(f"Saved: {fname}")
            self._logger.log_machine(f"SAVE file={fname}")
        except Exception as exc:
            self._stream_tab.set_save_status(f"Save error: {exc}", ok=False)
            self._logger.log_human(f"Save error: {exc}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _update_slave_tab_visibility(self, n_slaves: int) -> None:
        """Show only tabs for active slave count (keep stream + log always visible)."""
        # Remove all slave tabs first
        for st in self._slave_tabs:
            idx = self._tab_widget.indexOf(st)
            if idx >= 0:
                self._tab_widget.removeTab(idx)
        # Re-insert only active slave tabs after the stream tab (index 0)
        for i in range(n_slaves):
            if i < len(self._slave_tabs):
                # Insert before the log tab (always last)
                log_idx = self._tab_widget.indexOf(self._ta_log)
                self._tab_widget.insertTab(log_idx, self._slave_tabs[i],
                                           config.NODE_NAMES[i + 1])

    def _update_plot_visibility(self) -> None:
        """Compute visible node indices sorted by type (Hammer→Geo1→Geo2)."""
        visible_indices = [
            i for i in range(1, config.MAX_NODES)
            if self._data[i].visible and i <= self._n_slaves
        ]
        visible_indices.sort(key=lambda i: (
            self._TYPE_ORDER.get(
                self._slave_tabs[i - 1].dd_type.currentText()
                if i <= len(self._slave_tabs) else "", 99
            )
        ))
        self._plot_area.set_active_nodes(visible_indices)

    def _append_log_tab(self, line: str) -> None:
        """Thread-safe log line appended to the Log tab text area."""
        self._ta_log.append(line)
        # Trim to last 1000 lines
        doc = self._ta_log.document()
        while doc.blockCount() > 1000:
            cursor = self._ta_log.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    # ── Alias ─────────────────────────────────────────────────────────────────

    @pyqtSlot(int, str)
    def _on_alias_changed(self, node_idx: int, alias: str) -> None:
        self._settings.setValue(f"alias/node{node_idx}", alias)
        self._update_tab_title(node_idx)
        self._update_plot_visibility()   # reordenar plots al cambiar tipo

    _TYPE_ORDER = {"Hammer": 0, "Geo1": 1, "Geo2": 2}

    def _update_tab_title(self, node_idx: int) -> None:
        if not 1 <= node_idx <= len(self._slave_tabs):
            return
        st    = self._slave_tabs[node_idx - 1]
        title = st.dd_type.currentText() or config.NODE_NAMES[node_idx]
        tab_idx = self._tab_widget.indexOf(st)
        if tab_idx >= 0:
            self._tab_widget.setTabText(tab_idx, title)

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _toggle_theme(self) -> None:
        self._dark_mode = not self._dark_mode
        self._apply_theme()
        self._settings.setValue("ui/dark_mode", self._dark_mode)

    def _apply_theme(self) -> None:
        if self._dark_mode:
            QApplication.instance().setStyleSheet("")  # type: ignore[union-attr]
            self._btn_theme.setText("☀ Claro")
        else:
            QApplication.instance().setStyleSheet(_LIGHT_STYLESHEET)  # type: ignore[union-attr]
            self._btn_theme.setText("🌙 Oscuro")
        self._plot_area.set_theme(self._dark_mode)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._render_timer.stop()
        self._save_settings()
        self._on_disconnect()
        self._logger.log_human("Application closed")
        self._logger.close()
        event.accept()
