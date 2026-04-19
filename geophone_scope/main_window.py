"""
main_window.py — Geophone Filter Testbench GUI

UI state is driven exclusively by PSoC state packets (0xCC).
Commands are sent on user interaction; the UI only updates when PSoC confirms.
"""

import datetime
from collections import deque

import numpy as np
from scipy.io import savemat

import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QComboBox, QLabel, QGroupBox, QSpinBox,
    QDoubleSpinBox, QFileDialog, QCheckBox, QLineEdit, QSplitter,
    QTabWidget, QMessageBox, QTextEdit, QFrame
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QColor, QTextCharFormat, QTextCursor

import protocol as proto
import vdac_generator as vdacgen
from serial_worker import SerialWorker
from uart_worker import UartWorker
from digital_filters import ChannelFilter
from debug_protocol import format_event, DBG_CH_USB
from app_logger import log, log_path

SAMPLE_RATE = 1500
BUFFER_LEN  = 3000

CH_NAMES    = ['Ch1 Entrada cruda', 'Ch2 Post-Analógico', 'Ch3 Post-Digital']
VIRT_NAMES  = ['V-Ch1 Filtrado',    'V-Ch2 Filtrado',     'V-Ch3 Filtrado']
CH_COLORS   = ['#00BFFF', '#39FF14', '#FF6B35']
VIRT_COLORS = ['#AED6F1', '#A9DFBF', '#F0B27A']

# ADC config labels and effective ranges
_CFG_LABELS = ["CFG1  ±0.512 V  (18-bit)", "CFG2  ±1.024 V  (17-bit)",
               "CFG3  ±2.048 V  (17-bit)", "CFG4  ±6.144 V  (17-bit)"]
_BGAIN_LABELS = ["1×", "2×", "4×", "8×"]
_BGAIN_VALUES = [1, 2, 4, 8]

_SCALE_LABELS  = ["25%", "50%", "100%", "200%", "400%"]
_SCALE_FACTORS = [0.25, 0.50, 1.00, 2.00, 4.00]

_LEVEL_COLORS = {"INFO": "#B0BEC5", "WARN": "#FFA726", "ERROR": "#EF5350"}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Geophone Filter Testbench — Osciloscopio")
        self.resize(1450, 950)

        self._bufs      = [deque([0.0] * BUFFER_LEN, maxlen=BUFFER_LEN) for _ in range(3)]
        self._virt_bufs = [deque([0.0] * BUFFER_LEN, maxlen=BUFFER_LEN) for _ in range(3)]
        self._filters   = [ChannelFilter() for _ in range(3)]
        self._time_axis = np.linspace(-BUFFER_LEN / SAMPLE_RATE, 0, BUFFER_LEN)

        self._display_scale = 1.0
        self._updating_from_state = False   # suppress re-entrant signals
        self._streaming = False
        log(f"[APP] Inicio — log en: {log_path()}")

        # USB worker
        self._worker = SerialWorker()
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.state_update.connect(self._on_state_update)
        self._worker.debug_event.connect(self._on_debug_event)
        self._worker.connected.connect(self._on_connected)
        self._worker.disconnected.connect(self._on_disconnected)
        self._worker.error.connect(self._on_error)

        # UART debug worker (KitProg UART channel)
        self._uart = UartWorker()
        self._uart.debug_event.connect(self._on_debug_event)
        self._uart.error.connect(lambda m: self._append_debug_line(f"[E] UART: {m}", "ERROR"))

        self._build_ui()

        self._port_timer = QTimer()
        self._port_timer.timeout.connect(self._refresh_ports)
        self._port_timer.start(2000)
        self._refresh_ports()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        pg.setConfigOption('background', '#1A1A2E')
        pg.setConfigOption('foreground', '#E0E0E0')

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(4)
        root.addWidget(self._build_control_bar())

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, stretch=1)
        splitter.addWidget(self._build_plot_panel())
        splitter.addWidget(self._build_side_panel())
        splitter.setSizes([1000, 420])

    def _build_control_bar(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(6, 4, 6, 4)

        lay.addWidget(QLabel("Puerto:"))
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(200)
        lay.addWidget(self._port_combo)

        lay.addWidget(QLabel("Baudios:"))
        self._baud_combo = QComboBox()
        self._baud_combo.addItems(["9600", "19200", "38400", "57600",
                                   "115200", "230400", "460800", "921600"])
        self._baud_combo.setCurrentText("115200")
        self._baud_combo.setToolTip(
            "Para USB CDC el baudrate es ignorado por el hardware.\n"
            "Selecciona el baudrate solo si conectas via UART.")
        lay.addWidget(self._baud_combo)

        self._btn_autodetect = QPushButton("Auto-detectar PSoC")
        self._btn_autodetect.clicked.connect(self._autodetect_port)
        lay.addWidget(self._btn_autodetect)

        self._btn_connect = QPushButton("Conectar")
        self._btn_connect.setCheckable(True)
        self._btn_connect.toggled.connect(self._toggle_connect)
        self._btn_connect.setStyleSheet(
            "QPushButton:checked { background-color: #C0392B; color: white; }"
            "QPushButton { background-color: #27AE60; color: white;"
            " font-weight: bold; padding: 4px 14px; }")
        lay.addWidget(self._btn_connect)

        lay.addSpacing(8)

        self._btn_stream = QPushButton("▶ Iniciar")
        self._btn_stream.setCheckable(True)
        self._btn_stream.setEnabled(False)
        self._btn_stream.toggled.connect(self._toggle_streaming)
        self._btn_stream.setStyleSheet(
            "QPushButton:checked { background-color: #8E44AD; color: white; }"
            "QPushButton { background-color: #2C3E50; color: #BDC3C7;"
            " font-weight: bold; padding: 4px 14px; }")
        lay.addWidget(self._btn_stream)

        lay.addSpacing(16)

        self._btn_save = QPushButton("Guardar .mat")
        self._btn_save.clicked.connect(self._save_session)
        self._btn_save.setStyleSheet(
            "QPushButton { background-color: #2980B9; color: white; padding: 4px 10px; }")
        lay.addWidget(self._btn_save)

        lay.addStretch()

        self._status_label = QLabel("● Desconectado")
        self._status_label.setStyleSheet("color: #E74C3C; font-weight: bold;")
        lay.addWidget(self._status_label)

        return bar

    def _build_plot_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setSpacing(2)
        lay.setContentsMargins(0, 0, 0, 0)

        self._plot_widgets = []
        self._curves       = []
        self._virt_curves  = []

        for i in range(3):
            pw = pg.PlotWidget(title=CH_NAMES[i])
            pw.setLabel('left', 'Voltaje', units='V')
            pw.setLabel('bottom', 'Tiempo', units='s')
            pw.showGrid(x=True, y=True, alpha=0.3)
            pw.enableAutoRange(axis='y', enable=True)
            pw.addLegend(offset=(10, 10))

            curve = pw.plot(self._time_axis, list(self._bufs[i]),
                            pen=pg.mkPen(CH_COLORS[i], width=1.5),
                            name=CH_NAMES[i])
            virt  = pw.plot(self._time_axis, list(self._virt_bufs[i]),
                            pen=pg.mkPen(VIRT_COLORS[i], width=1.5, style=Qt.DashLine),
                            name=VIRT_NAMES[i])
            virt.setVisible(False)

            self._plot_widgets.append(pw)
            self._curves.append(curve)
            self._virt_curves.append(virt)
            lay.addWidget(pw, stretch=1)

        return panel

    def _build_side_panel(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_psoc_config_tab(), "Config PSoC")
        tabs.addTab(self._build_vdac_tab(),        "VDAC / Señal")
        tabs.addTab(self._build_filter_tab(),      "Filtros Digitales")
        tabs.addTab(self._build_debug_tab(),        "Debug")
        return tabs

    # ── Config PSoC tab ───────────────────────────────────────────────────────

    def _build_psoc_config_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # Signal routing
        grp_sig = QGroupBox("Ruteo de señal")
        g = QGridLayout(grp_sig)

        g.addWidget(QLabel("Entrada:"), 0, 0)
        self._mux_in = QComboBox()
        self._mux_in.addItems(["Geófono (SAR)", "VDAC (señal de prueba)"])
        self._mux_in.currentIndexChanged.connect(self._on_mux_in_changed)
        g.addWidget(self._mux_in, 0, 1)

        g.addWidget(QLabel("Filtro salida:"), 1, 0)
        self._mux_out = QComboBox()
        self._mux_out.addItems(["Filtro 1 — TIA (1° orden)", "Filtro 2 — Opa (2° orden)"])
        self._mux_out.currentIndexChanged.connect(self._on_mux_out_changed)
        g.addWidget(self._mux_out, 1, 1)

        lay.addWidget(grp_sig)

        # ADC hardware config
        grp_adc = QGroupBox("ADC DelSig — configuración hardware")
        ga = QGridLayout(grp_adc)

        ga.addWidget(QLabel("Rango entrada:"), 0, 0)
        self._adc_cfg_combo = QComboBox()
        self._adc_cfg_combo.addItems(_CFG_LABELS)
        self._adc_cfg_combo.setCurrentIndex(3)   # CFG4 default
        self._adc_cfg_combo.currentIndexChanged.connect(self._on_adc_cfg_changed)
        ga.addWidget(self._adc_cfg_combo, 0, 1)

        ga.addWidget(QLabel("Buffer gain:"), 1, 0)
        self._buf_gain_combo = QComboBox()
        self._buf_gain_combo.addItems(_BGAIN_LABELS)
        self._buf_gain_combo.setCurrentIndex(3)   # 8× default
        self._buf_gain_combo.currentIndexChanged.connect(self._on_buf_gain_changed)
        ga.addWidget(self._buf_gain_combo, 1, 1)

        ga.addWidget(QLabel("Rango efectivo:"), 2, 0)
        self._effective_range_lbl = QLabel("±6.144 V")
        self._effective_range_lbl.setStyleSheet("color: #00BFFF; font-weight: bold;")
        ga.addWidget(self._effective_range_lbl, 2, 1)

        ga.addWidget(QLabel("Nota CFG1:"), 3, 0)
        note = QLabel("Buffer gain = 1 fijo\n(límite SPS 1464)")
        note.setStyleSheet("color: #F39C12; font-size: 10px;")
        ga.addWidget(note, 3, 1)

        lay.addWidget(grp_adc)

        # Display scale (visual only, no PSoC command)
        grp_disp = QGroupBox("Escala de visualización  (solo pantalla, no cambia PSoC)")
        gd = QHBoxLayout(grp_disp)
        gd.addWidget(QLabel("Escala:"))
        self._scale_combo = QComboBox()
        self._scale_combo.addItems(_SCALE_LABELS)
        self._scale_combo.setCurrentIndex(2)   # 100%
        self._scale_combo.currentIndexChanged.connect(self._on_scale_changed)
        gd.addWidget(self._scale_combo)
        gd.addStretch()

        lay.addWidget(grp_disp)
        lay.addStretch()
        return w

    # ── VDAC / Señal tab ──────────────────────────────────────────────────────

    def _build_vdac_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        grp = QGroupBox("Generador de señal VDAC")
        g = QGridLayout(grp)

        g.addWidget(QLabel("Forma de onda:"), 0, 0)
        self._wave_combo = QComboBox()
        self._wave_combo.addItems(["Ricker", "Gauss 3ra deriv.", "Senoidal",
                                   "Chirp 10-240Hz", "Impulso", "Silencio",
                                   "Archivo CSV..."])
        g.addWidget(self._wave_combo, 0, 1)

        g.addWidget(QLabel("σ / frec. (s o Hz):"), 1, 0)
        self._wave_param = QDoubleSpinBox()
        self._wave_param.setRange(0.001, 500.0); self._wave_param.setValue(0.1)
        self._wave_param.setSingleStep(0.01)
        g.addWidget(self._wave_param, 1, 1)

        g.addWidget(QLabel("Amplitud (0-1):"), 2, 0)
        self._wave_amp = QDoubleSpinBox()
        self._wave_amp.setRange(0.01, 1.0); self._wave_amp.setValue(0.9)
        self._wave_amp.setSingleStep(0.05)
        g.addWidget(self._wave_amp, 2, 1)

        self._btn_load_vdac = QPushButton("Cargar en PSoC ▶")
        self._btn_load_vdac.clicked.connect(self._load_vdac)
        g.addWidget(self._btn_load_vdac, 3, 0, 1, 2)

        lay.addWidget(grp)
        lay.addStretch()
        return w

    # ── Filtros tab ───────────────────────────────────────────────────────────

    def _build_filter_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        for i in range(3):
            grp = QGroupBox(f"Canal {i+1} — Filtro virtual")
            g = QGridLayout(grp)

            tc = QComboBox()
            tc.addItems(["Ninguno", "Notch", "Pasa-banda", "Pasa-altas",
                         "Pasa-bajas", "FIR (coef. manual)", "IIR SOS (manual)"])
            g.addWidget(QLabel("Tipo:"), 0, 0); g.addWidget(tc, 0, 1)

            f1 = QDoubleSpinBox(); f1.setRange(0.1, 700.0); f1.setValue(240.0)
            g.addWidget(QLabel("f1 / fc (Hz):"), 1, 0); g.addWidget(f1, 1, 1)

            f2 = QDoubleSpinBox(); f2.setRange(0.1, 700.0); f2.setValue(300.0)
            g.addWidget(QLabel("f2 / Q:"), 2, 0); g.addWidget(f2, 2, 1)

            ordr = QSpinBox(); ordr.setRange(1, 10); ordr.setValue(4)
            g.addWidget(QLabel("Orden:"), 3, 0); g.addWidget(ordr, 3, 1)

            coef = QLineEdit(); coef.setPlaceholderText("Coeficientes separados por comas")
            coef.setVisible(False); g.addWidget(coef, 4, 0, 1, 2)

            chk = QCheckBox("Mostrar canal virtual"); g.addWidget(chk, 5, 0, 1, 2)
            btn = QPushButton("Aplicar");             g.addWidget(btn, 6, 0, 1, 2)

            lay.addWidget(grp)

            def make_apply(idx, tc_, f1_, f2_, ordr_, coef_, chk_):
                def apply():
                    self._apply_filter(idx, tc_.currentIndex(),
                                       f1_.value(), f2_.value(),
                                       ordr_.value(), coef_.text())
                    self._virt_curves[idx].setVisible(chk_.isChecked())
                return apply

            tc.currentIndexChanged.connect(lambda ti, c=coef: c.setVisible(ti in (5, 6)))
            btn.clicked.connect(make_apply(i, tc, f1, f2, ordr, coef, chk))
            chk.stateChanged.connect(
                lambda s, ci=i: self._virt_curves[ci].setVisible(s == Qt.Checked))

        lay.addStretch()
        return w

    # ── Debug tab ─────────────────────────────────────────────────────────────

    def _build_debug_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # UART debug connection
        grp_uart = QGroupBox("UART debug (KitProg USB-UART)")
        gu = QGridLayout(grp_uart)

        gu.addWidget(QLabel("Puerto UART:"), 0, 0)
        self._uart_combo = QComboBox()
        self._uart_combo.setMinimumWidth(160)
        gu.addWidget(self._uart_combo, 0, 1)

        gu.addWidget(QLabel("Baudios UART:"), 1, 0)
        self._uart_baud_combo = QComboBox()
        self._uart_baud_combo.addItems(["9600", "19200", "38400", "57600", "115200", "230400"])
        self._uart_baud_combo.setCurrentText("115200")
        gu.addWidget(self._uart_baud_combo, 1, 1)

        self._btn_uart = QPushButton("Conectar UART")
        self._btn_uart.setCheckable(True)
        self._btn_uart.toggled.connect(self._toggle_uart)
        self._btn_uart.setStyleSheet(
            "QPushButton:checked { background-color: #8E44AD; color: white; }"
            "QPushButton { background-color: #555; color: white; padding: 3px 8px; }")
        gu.addWidget(self._btn_uart, 2, 0, 1, 2)

        lay.addWidget(grp_uart)

        # Debug control row (USB CDC)
        grp_ctrl = QGroupBox("Control de debug (vía USB CDC)")
        gc = QGridLayout(grp_ctrl)

        gc.addWidget(QLabel("Debug:"), 0, 0)
        self._btn_debug_en = QPushButton("Activar debug")
        self._btn_debug_en.setCheckable(True)
        self._btn_debug_en.toggled.connect(self._on_debug_enable_toggled)
        self._btn_debug_en.setStyleSheet(
            "QPushButton:checked { background-color: #27AE60; color: white; }"
            "QPushButton { background-color: #555; color: white; padding: 3px 8px; }")
        gc.addWidget(self._btn_debug_en, 0, 1)

        gc.addWidget(QLabel("Canal debug:"), 1, 0)
        self._debug_ch_combo = QComboBox()
        self._debug_ch_combo.addItems(["USB", "UART", "Ambos"])
        self._debug_ch_combo.currentIndexChanged.connect(self._on_debug_ch_changed)
        gc.addWidget(self._debug_ch_combo, 1, 1)

        gc.addWidget(QLabel("Pedir estado:"), 2, 0)
        btn_state = QPushButton("CMD_GET_STATE")
        btn_state.clicked.connect(lambda: (
            log("[GUI] CLICK CMD_GET_STATE"),
            self._worker.send(proto.cmd_get_state())
        ))
        gc.addWidget(btn_state, 2, 1)

        lay.addWidget(grp_ctrl)

        # Log area
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setFont(QFont("Consolas", 9))
        self._log_area.setStyleSheet(
            "background:#0D0D1A; color:#B0BEC5; border:1px solid #333;")
        self._log_area.setMinimumHeight(200)
        lay.addWidget(self._log_area, stretch=1)

        btn_row = QWidget()
        br = QHBoxLayout(btn_row)
        br.setContentsMargins(0, 0, 0, 0)
        btn_clear = QPushButton("Limpiar")
        btn_clear.clicked.connect(self._log_area.clear)
        btn_export = QPushButton("Exportar log")
        btn_export.clicked.connect(self._export_log)
        br.addStretch()
        br.addWidget(btn_clear)
        br.addWidget(btn_export)
        lay.addWidget(btn_row)

        return w

    # ── Filter application ────────────────────────────────────────────────────

    def _apply_filter(self, idx, ftype, f1, f2, order, coef_str):
        filt = self._filters[idx]
        fs = float(SAMPLE_RATE)
        if ftype == 0:   filt.clear()
        elif ftype == 1: filt.set_notch(f1, f2, fs)
        elif ftype == 2: filt.set_bandpass(f1, f2, fs, order)
        elif ftype == 3: filt.set_highpass(f1, fs, order)
        elif ftype == 4: filt.set_lowpass(f1, fs, order)
        elif ftype == 5:
            try:
                filt.set_fir([float(c.strip()) for c in coef_str.split(',') if c.strip()])
            except ValueError:
                QMessageBox.warning(self, "Error", "Coeficientes FIR inválidos.")
                return
        elif ftype == 6:
            try:
                sos = [[float(x) for x in r.split(',')] for r in coef_str.strip().split(';')]
                filt.set_iir_sos(sos)
            except ValueError:
                QMessageBox.warning(self, "Error", "Formato SOS inválido.")
                return

        data = np.array(self._bufs[idx])
        filt.reset()
        if filt.active:
            filtered = filt.process(data)
            self._virt_bufs[idx] = deque(filtered.tolist(), maxlen=BUFFER_LEN)
            self._virt_curves[idx].setData(self._time_axis, list(self._virt_bufs[idx]))

    # ── State sync (PSoC → UI) ────────────────────────────────────────────────

    def _on_state_update(self, st: dict):
        """Update all UI controls to reflect confirmed PSoC state. Never fires commands."""
        log(f"[GUI] state_update recibido: {st}")
        self._updating_from_state = True
        try:
            cfg  = st.get("adc_cfg", 4)
            gain = st.get("buf_gain", 8)

            self._mux_in.setCurrentIndex(st.get("vdac_mode", 0))
            self._mux_out.setCurrentIndex(st.get("filter_sel", 0))

            cfg_idx = max(0, min(cfg - 1, 3))
            self._adc_cfg_combo.setCurrentIndex(cfg_idx)

            # CFG1 locks buffer gain to 1
            if cfg == 1:
                self._buf_gain_combo.setEnabled(False)
                self._buf_gain_combo.setCurrentIndex(0)   # 1×
            else:
                self._buf_gain_combo.setEnabled(True)
                try:
                    gi = _BGAIN_VALUES.index(gain)
                except ValueError:
                    gi = 3
                self._buf_gain_combo.setCurrentIndex(gi)

            self._update_effective_range_label(cfg, gain)

            dbg_en = st.get("debug_en", False)
            self._btn_debug_en.setChecked(dbg_en)
            self._btn_debug_en.setText("Debug ON" if dbg_en else "Activar debug")

            dbg_ch = st.get("debug_ch", DBG_CH_UART)
            self._debug_ch_combo.setCurrentIndex(min(dbg_ch, 2))

        finally:
            self._updating_from_state = False

    def _update_effective_range_label(self, cfg: int, buf_gain: int):
        vfs = proto.effective_fullscale(cfg, buf_gain)
        self._effective_range_lbl.setText(f"±{vfs:.3f} V")

    # ── Data handler ──────────────────────────────────────────────────────────

    _data_log_count = 0

    def _on_data_ready(self, packets: list):
        self._data_log_count += 1
        if self._data_log_count <= 3 or self._data_log_count % 100 == 0:
            sample = packets[0] if packets else {}
            log(f"[GUI] data_ready #{self._data_log_count}: {len(packets)} pkts | sample={sample}")
        ch_new = [[], [], []]
        for pkt in packets:
            ch_new[0].append(pkt['raw_input']    * self._display_scale)
            ch_new[1].append(pkt['post_analog']  * self._display_scale)
            ch_new[2].append(pkt['post_digital'] * self._display_scale)

        for i in range(3):
            if not ch_new[i]:
                continue
            arr = np.array(ch_new[i])
            self._bufs[i].extend(arr.tolist())
            if self._filters[i].active:
                self._virt_bufs[i].extend(self._filters[i].process(arr).tolist())
            else:
                self._virt_bufs[i].extend(arr.tolist())

            self._curves[i].setData(self._time_axis, list(self._bufs[i]))
            if self._virt_curves[i].isVisible():
                self._virt_curves[i].setData(self._time_axis, list(self._virt_bufs[i]))

    # ── Debug event handler ───────────────────────────────────────────────────

    def _on_debug_event(self, evt: dict):
        self._append_debug_line(format_event(evt), evt.get("level", "INFO"))

    def _append_debug_line(self, text: str, level: str = "INFO"):
        color = _LEVEL_COLORS.get(level, "#B0BEC5")
        cursor = self._log_area.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(text + "\n", fmt)
        self._log_area.setTextCursor(cursor)
        self._log_area.ensureCursorVisible()

    # ── Connection handlers ───────────────────────────────────────────────────

    def _on_connected(self):
        log("[GUI] Conectado USB — enviando cmd_start + cmd_get_state")
        self._status_label.setText("● Conectado USB")
        self._status_label.setStyleSheet("color: #2ECC71; font-weight: bold;")
        self._btn_connect.setText("Desconectar")
        self._btn_stream.setEnabled(True)
        self._btn_stream.setChecked(True)
        self._btn_stream.setText("■ Detener")
        self._worker.send(proto.cmd_start())
        self._worker.send(proto.cmd_get_state())
        self._streaming = True

    def _on_disconnected(self):
        log("[GUI] Desconectado USB")
        self._status_label.setText("● Desconectado")
        self._status_label.setStyleSheet("color: #E74C3C; font-weight: bold;")
        self._btn_connect.setText("Conectar")
        self._btn_connect.setChecked(False)
        self._btn_stream.setChecked(False)
        self._btn_stream.setText("▶ Iniciar")
        self._btn_stream.setEnabled(False)
        self._streaming = False

    def _on_error(self, msg: str):
        log(f"[GUI] USB error: {msg}")
        self._append_debug_line(f"[E] USB error: {msg}", "ERROR")
        self._btn_connect.setChecked(False)

    # ── Control handlers (send commands — UI updates only on state packet) ────

    def _toggle_connect(self, checked: bool):
        if checked:
            text = self._port_combo.currentText()
            port = self._port_combo.currentData() or text.split()[0]
            if not port:
                QMessageBox.warning(self, "Sin puerto", "Seleccioná un puerto COM primero.")
                self._btn_connect.setChecked(False)
                return
            baud = int(self._baud_combo.currentText())
            log(f"[GUI] CLICK Conectar → port={port} baud={baud}")
            self._worker.set_port(port, baud)
            self._worker.start()
        else:
            log("[GUI] CLICK Desconectar")
            self._worker.send(proto.cmd_stop())
            self._worker.stop()

    def _toggle_streaming(self, checked: bool):
        if checked:
            log("[GUI] CLICK Iniciar streaming → cmd_start")
            self._worker.send(proto.cmd_start())
            self._btn_stream.setText("■ Detener")
            self._streaming = True
        else:
            log("[GUI] CLICK Detener streaming → cmd_stop")
            self._worker.send(proto.cmd_stop())
            self._btn_stream.setText("▶ Iniciar")
            self._streaming = False

    def _toggle_uart(self, checked: bool):
        if checked:
            port = self._uart_combo.currentData()
            if not port:
                text = self._uart_combo.currentText()
                port = text.split()[0] if text else None
            if not port:
                QMessageBox.warning(self, "Sin puerto UART", "Seleccioná el puerto UART primero.")
                self._btn_uart.setChecked(False)
                return
            baud = int(self._uart_baud_combo.currentText())
            log(f"[GUI] CLICK Conectar UART → port={port} baud={baud}")
            self._uart.set_port(port, baud)
            self._uart.start()
            self._btn_uart.setText("Desconectar UART")
        else:
            log("[GUI] CLICK Desconectar UART")
            self._uart.stop()
            self._btn_uart.setText("Conectar UART")

    def _on_mux_in_changed(self, idx: int):
        if self._updating_from_state: return
        log(f"[GUI] CLICK Entrada → {idx} (cmd_set_mux_in)")
        self._worker.send(proto.cmd_set_mux_in(idx))

    def _on_mux_out_changed(self, idx: int):
        if self._updating_from_state: return
        log(f"[GUI] CLICK Filtro salida → {idx} (cmd_set_mux_out)")
        self._worker.send(proto.cmd_set_mux_out(idx))

    def _on_adc_cfg_changed(self, idx: int):
        if self._updating_from_state: return
        cfg      = idx + 1
        gain_idx = self._buf_gain_combo.currentIndex()
        bg       = _BGAIN_VALUES[gain_idx] if cfg != 1 else 1
        log(f"[GUI] CLICK ADC cfg → cfg={cfg} bg={bg} (cmd_set_adc)")
        self._worker.send(proto.cmd_set_adc(cfg, bg))

    def _on_buf_gain_changed(self, idx: int):
        if self._updating_from_state: return
        cfg = self._adc_cfg_combo.currentIndex() + 1
        bg  = _BGAIN_VALUES[idx]
        log(f"[GUI] CLICK Buffer gain → {bg}x (cmd_set_adc cfg={cfg})")
        self._worker.send(proto.cmd_set_adc(cfg, bg))

    def _on_scale_changed(self, idx: int):
        log(f"[GUI] CLICK Escala → {_SCALE_LABELS[idx]}")
        self._display_scale = _SCALE_FACTORS[idx]

    def _on_debug_enable_toggled(self, checked: bool):
        if self._updating_from_state: return
        log(f"[GUI] CLICK Debug enable → {checked}")
        self._worker.send(proto.cmd_set_debug(checked))

    def _on_debug_ch_changed(self, idx: int):
        if self._updating_from_state: return
        log(f"[GUI] CLICK Debug channel → {idx}")
        self._worker.send(proto.cmd_set_debug_ch(idx))

    def _load_vdac(self):
        wi  = self._wave_combo.currentIndex()
        par = self._wave_param.value()
        amp = self._wave_amp.value()
        log(f"[GUI] CLICK Cargar VDAC → wave_idx={wi} param={par} amp={amp}")

        if   wi == 0: seq = vdacgen.ricker(par, amplitude=amp)
        elif wi == 1: seq = vdacgen.gauss3(par, amplitude=amp)
        elif wi == 2: seq = vdacgen.sine(par, amplitude=amp)
        elif wi == 3: seq = vdacgen.chirp(10.0, 240.0, amplitude=amp)
        elif wi == 4: seq = vdacgen.impulse(amplitude=amp)
        elif wi == 5: seq = vdacgen.silence()
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Cargar CSV", "", "CSV (*.csv)")
            if not path: return
            seq = vdacgen.from_csv(path, amplitude=amp)

        self._worker.send(proto.cmd_vdac_load(seq))

    # ── Port management ───────────────────────────────────────────────────────

    @staticmethod
    def _is_kitprog(port) -> bool:
        """KitProg USB-UART is a debug bridge, not the PSoC CDC — exclude from USB list."""
        desc = (port.description or "").lower()
        return "kitprog" in desc

    def _refresh_ports(self):
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())

        # USB port combo — exclude KitProg USB-UART (debug bridge, not PSoC CDC)
        current_usb = self._port_combo.currentData()
        self._port_combo.clear()
        for p in ports:
            if self._is_kitprog(p):
                continue
            label = f"{p.device}  ({p.description})" if p.description and p.description != "n/a" else p.device
            self._port_combo.addItem(label, userData=p.device)
        if current_usb:
            for i in range(self._port_combo.count()):
                if self._port_combo.itemData(i) == current_usb:
                    self._port_combo.setCurrentIndex(i)
                    break

        # UART port combo — show all ports (KitProg is valid as UART debug source)
        current_uart = self._uart_combo.currentData()
        self._uart_combo.clear()
        for p in ports:
            label = f"{p.device}  ({p.description})" if p.description and p.description != "n/a" else p.device
            self._uart_combo.addItem(label, userData=p.device)
        if current_uart:
            for i in range(self._uart_combo.count()):
                if self._uart_combo.itemData(i) == current_uart:
                    self._uart_combo.setCurrentIndex(i)
                    break

    def _autodetect_port(self):
        port = SerialWorker.detect_psoc_port()
        if port:
            for i in range(self._port_combo.count()):
                if self._port_combo.itemData(i) == port:
                    self._port_combo.setCurrentIndex(i)
                    break
            self._append_debug_line(f"[I] PSoC detectado en {port}", "INFO")
        else:
            self._append_debug_line("[W] PSoC no encontrado (VID 0x04B4)", "WARN")

    # ── Save session ──────────────────────────────────────────────────────────

    def _save_session(self):
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar sesión", f"session_{ts}.mat", "MAT-file (*.mat)")
        if not path: return

        cfg  = self._adc_cfg_combo.currentIndex() + 1
        gain = _BGAIN_VALUES[self._buf_gain_combo.currentIndex()]
        t    = np.linspace(-BUFFER_LEN / SAMPLE_RATE, 0.0, BUFFER_LEN)

        savemat(path, {
            'sample_rate':       np.array([SAMPLE_RATE]),
            'time':              t,
            'ch1_raw_input':     np.array(self._bufs[0]),
            'ch2_post_analog':   np.array(self._bufs[1]),
            'ch3_post_digital':  np.array(self._bufs[2]),
            'virt_ch1_filtered': np.array(self._virt_bufs[0]),
            'virt_ch2_filtered': np.array(self._virt_bufs[1]),
            'virt_ch3_filtered': np.array(self._virt_bufs[2]),
            'adc_cfg':           np.array([cfg]),
            'buf_gain':          np.array([gain]),
            'display_scale':     np.array([self._display_scale]),
            'mux_in':            np.array([self._mux_in.currentIndex()]),
            'mux_out':           np.array([self._mux_out.currentIndex()]),
            'timestamp':         ts,
        })

    def _export_log(self):
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar log", f"debug_{ts}.txt", "Texto (*.txt)")
        if not path: return
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self._log_area.toPlainText())

    def closeEvent(self, event):
        log("[APP] Cerrando aplicación")
        self._port_timer.stop()
        if self._worker.isRunning():
            self._worker.send(proto.cmd_stop())
            self._worker.stop()
        if self._uart.isRunning():
            self._uart.stop()
        event.accept()
