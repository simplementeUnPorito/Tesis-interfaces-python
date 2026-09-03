"""Modo gráfico del banco: la ventana desde la que se prueba una placa.

Arquitectura, y es una regla dura: **todo el I/O serie vive en un QThread y la
ventana nunca lo llama directo.** ``Session`` es sincrónica y sus métodos tardan
minutos —el grupo D son unos dos— así que llamarlos desde el hilo de Qt congela
la ventana entera. El worker recibe trabajos por una cola, los ejecuta uno por
vez y devuelve todo por señales. La ventana sólo toca widgets.

Cinco pestañas, dos modos:

* Acciones, Checklist y Consola son el **modo automático**: la corrida completa,
  los grupos sueltos y el diálogo crudo con la placa.
* Experimentos es el **modo manual**: mover los IDAC, mirar cualquier tap,
  barrer una etapa y un osciloscopio lento en vivo.
* Gráficos y Replay funcionan sin placa conectada.
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core import console as con
from .core import figures
from .core.checklist import (
    D2_MIN_SLOPE_UV_PLACA,
    STAGE_NAMES,
    TAP_NAMES,
    ChecklistParser,
    Item,
    evaluate,
)
from .core.lab import GAIN_CODES, SETTLE_MS, Lab, MonSample
from .core.session import GROUPS, RunResult, Session

HW_PARTS = ("oled", "btn", "geo", "sd", "psoc")
HW_LABELS = ("ausente", "presente", "auto")


# ==========================================================================
# Worker de serie
# ==========================================================================
class SerialWorker(QThread):
    """Dueño único de la ``Console`` y la ``Session``.

    Todo lo que quiera hablar con la placa encola un trabajo con ``submit()``.
    Los trabajos se ejecutan de a uno: el firmware tiene una sola consola y
    solaparle dos corridas es pedirle un diagnóstico falso.
    """

    entry_received = pyqtSignal(object)      # console.Entry
    line_received = pyqtSignal(str)
    item_received = pyqtSignal(object)       # checklist.Item
    phase_changed = pyqtSignal(str)
    connection_changed = pyqtSignal(bool, str)
    job_started = pyqtSignal(str)
    job_finished = pyqtSignal(str, object)
    job_failed = pyqtSignal(str, str)
    state_changed = pyqtSignal(object)       # (link, sync_armed, hw)

    def __init__(self, port: str, parent=None) -> None:
        super().__init__(parent)
        self._port = port
        self._jobs: queue.Queue = queue.Queue()
        self._running = False
        self.console: Optional[con.Console] = None
        self.session: Optional[Session] = None
        self.lab: Optional[Lab] = None

    # -- API desde el hilo de la GUI --------------------------------------
    def submit(self, name: str, fn: Callable[[Session], Any]) -> None:
        """Encola un trabajo. ``fn`` recibe la Session y corre en el worker."""
        self._jobs.put((name, fn))

    def stop(self) -> None:
        self._running = False
        self._jobs.put(None)

    # -- hilo --------------------------------------------------------------
    def run(self) -> None:
        self._running = True
        try:
            self.console = con.Console(self._port, on_entry=self._on_entry)
            listo = self.console.open(wait_ready=True, timeout=25.0)
        except Exception as exc:
            self.connection_changed.emit(False, f"{type(exc).__name__}: {exc}")
            return

        self.session = Session(
            self.console,
            on_line=self.line_received.emit,
            on_item=self.item_received.emit,
            on_phase=self.phase_changed.emit,
        )
        self.lab = Lab(self.session)
        self.connection_changed.emit(
            True,
            f"{self._port} conectado"
            + ("" if listo else " (sin banner de arranque: ¿firmware de campo?)"),
        )

        while self._running:
            try:
                trabajo = self._jobs.get(timeout=0.05)
            except queue.Empty:
                # Bombear igual: así se ven los eventos que el PSoC manda solo.
                if self.console is not None and self.console.is_open:
                    for linea in self.console.poll():
                        self.session.parser.feed(linea)
                        self.line_received.emit(linea)
                continue

            if trabajo is None:
                break
            nombre, fn = trabajo
            self.job_started.emit(nombre)
            try:
                resultado = fn(self.session)
                self.job_finished.emit(nombre, resultado)
            except Exception as exc:
                self.job_failed.emit(nombre, f"{type(exc).__name__}: {exc}")
            finally:
                self._emit_state()

        if self.console is not None:
            self.console.close()
        self.connection_changed.emit(False, "desconectado")

    # -- internos ----------------------------------------------------------
    def _on_entry(self, entry: con.Entry) -> None:
        self.entry_received.emit(entry)

    def _emit_state(self) -> None:
        if self.session is not None:
            self.state_changed.emit(
                (self.session.parser.link, self.session.sync_armed,
                 dict(self.session.parser.hw))
            )


# ==========================================================================
# Utilidades de presentación
# ==========================================================================
def _mono() -> QFont:
    f = QFont("Consolas")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(9)
    return f


class FigurePane(QWidget):
    """Un canvas de matplotlib con un botón para guardarlo."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

        self._CanvasClass = FigureCanvasQTAgg
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._canvas = None
        self._figure = None
        self._placeholder = QLabel("Todavía no hay datos para graficar.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(f"color: {figures.MUTED};")
        self._layout.addWidget(self._placeholder)

    def show_figure(self, fig) -> None:
        import matplotlib.pyplot as plt

        if self._canvas is not None:
            self._layout.removeWidget(self._canvas)
            self._canvas.setParent(None)
        if self._figure is not None:
            plt.close(self._figure)
        self._placeholder.hide()
        self._figure = fig
        self._canvas = self._CanvasClass(fig)
        self._layout.addWidget(self._canvas)
        self._canvas.draw_idle()

    def show_message(self, texto: str) -> None:
        if self._canvas is not None:
            self._layout.removeWidget(self._canvas)
            self._canvas.setParent(None)
            self._canvas = None
        self._placeholder.setText(texto)
        self._placeholder.show()

    def save(self, parent: QWidget) -> None:
        if self._figure is None:
            QMessageBox.information(parent, "Guardar", "No hay ninguna figura dibujada.")
            return
        ruta, _ = QFileDialog.getSaveFileName(parent, "Guardar figura", "figura.png",
                                              "PNG (*.png)")
        if ruta:
            self._figure.savefig(ruta, facecolor=figures.SURFACE, bbox_inches="tight")


# ==========================================================================
# Ventana
# ==========================================================================
class MainWindow(QMainWindow):
    def __init__(self, port: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle("Banco de placas — nodo esclavo")
        self.resize(1220, 820)

        self.worker: Optional[SerialWorker] = None
        #: Parser que alimentan el replay y las corridas sin placa.
        self.offline_parser = ChecklistParser()
        self._mon_samples: list[MonSample] = []
        self._last_sweep = None
        self._busy = False

        raiz = QWidget()
        self.setCentralWidget(raiz)
        vbox = QVBoxLayout(raiz)
        vbox.setContentsMargins(8, 8, 8, 8)

        vbox.addWidget(self._build_topbar(port))
        self.tabs = QTabWidget()
        vbox.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_actions_tab(), "Acciones")
        self.tabs.addTab(self._build_checklist_tab(), "Checklist")
        self.tabs.addTab(self._build_console_tab(), "Consola")
        self.tabs.addTab(self._build_lab_tab(), "Experimentos")
        self.tabs.addTab(self._build_plots_tab(), "Gráficos")

        self.statusBar().showMessage("Sin conectar")
        self._set_busy(False)

    # -- barra superior ----------------------------------------------------
    def _build_topbar(self, port: Optional[str]) -> QWidget:
        caja = QGroupBox()
        h = QHBoxLayout(caja)

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(230)
        self._refresh_ports(port)
        h.addWidget(QLabel("Puerto:"))
        h.addWidget(self.port_combo)

        b = QPushButton("Buscar")
        b.clicked.connect(lambda: self._refresh_ports(None))
        h.addWidget(b)

        self.btn_connect = QPushButton("Conectar")
        self.btn_connect.clicked.connect(self._toggle_connection)
        h.addWidget(self.btn_connect)

        self.btn_reset = QPushButton("Reset ESP")
        self.btn_reset.setToolTip(
            "Resetea el ESP por RTS. Ojo: eso desarma el SYNC y hay que volver "
            "a correr el grupo B antes de cualquier captura."
        )
        self.btn_reset.clicked.connect(self._reset_esp)
        h.addWidget(self.btn_reset)

        h.addSpacing(16)
        self.lbl_link = QLabel("enlace: ?")
        self.lbl_link.setStyleSheet(f"color: {figures.MUTED}; font-weight: bold;")
        h.addWidget(self.lbl_link)

        self.lbl_sync = QLabel("SYNC: sin armar")
        self.lbl_sync.setStyleSheet(f"color: {figures.MUTED};")
        h.addWidget(self.lbl_sync)

        h.addStretch(1)
        self.lbl_phase = QLabel("")
        self.lbl_phase.setStyleSheet(f"color: {figures.INK_2};")
        h.addWidget(self.lbl_phase)
        return caja

    def _refresh_ports(self, preferido: Optional[str]) -> None:
        self.port_combo.clear()
        p = con.find_ports()
        for dev in p["esp"]:
            self.port_combo.addItem(f"{dev}  (ESP32, es este)", dev)
        for dev in p["psoc"]:
            self.port_combo.addItem(f"{dev}  (KitProg del PSoC, NO sirve acá)", dev)
        for dev in p["otros"]:
            self.port_combo.addItem(f"{dev}", dev)
        if preferido:
            for i in range(self.port_combo.count()):
                if self.port_combo.itemData(i) == preferido:
                    self.port_combo.setCurrentIndex(i)
                    break

    # -- pestaña Acciones ---------------------------------------------------
    def _build_actions_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)

        corridas = QGroupBox("Modo automático")
        cv = QVBoxLayout(corridas)
        self._btn_run = QPushButton("Corrida completa (A+B+C+D)")
        self._btn_run.clicked.connect(lambda: self._job(
            "corrida completa", lambda s: s.run_full(), self._on_run_result))
        cv.addWidget(self._btn_run)
        for letra in ("a", "b", "c", "d"):
            desc = GROUPS[letra][0]
            b = QPushButton(desc)
            b.clicked.connect(
                lambda _c=False, L=letra: self._job(
                    f"grupo {L}", lambda s, L=L: s.run_group(L), self._on_run_result))
            cv.addWidget(b)
        cv.addStretch(1)
        grid.addWidget(corridas, 0, 0)

        inter = QGroupBox("Con operador")
        iv = QVBoxLayout(inter)
        b = QPushButton("Los cuatro pulsadores del ESP")
        b.clicked.connect(lambda: self._job("botones", lambda s: s.buttons_esp(),
                                            self._on_run_result))
        iv.addWidget(b)
        b = QPushButton("Pulsador del PSoC")
        b.clicked.connect(lambda: self._job("boton", lambda s: s.button_psoc(),
                                            self._on_run_result))
        iv.addWidget(b)
        fila = QHBoxLayout()
        self.spin_taps = QSpinBox()
        self.spin_taps.setRange(1, 50)
        self.spin_taps.setValue(8)
        fila.addWidget(QLabel("intentos:"))
        fila.addWidget(self.spin_taps)
        b = QPushButton("Golpe al geófono (D7)")
        b.setToolTip("Golpeá a ritmo parejo, uno cada dos segundos: cada intento "
                     "captura 1,47 s y varios van a caer bien.")
        b.clicked.connect(self._run_taps)
        fila.addWidget(b, 1)
        iv.addLayout(fila)
        iv.addStretch(1)
        grid.addWidget(inter, 0, 1)

        perfil = QGroupBox("Perfil de hardware presente")
        pf = QFormLayout(perfil)
        self.hw_combos: dict[str, QComboBox] = {}
        for parte in HW_PARTS:
            cb = QComboBox()
            cb.addItems(HW_LABELS)
            cb.setCurrentIndex(2)
            cb.activated.connect(
                lambda idx, P=parte: self._job(
                    f"hw {P}", lambda s, P=P, I=idx: s.set_hw(P, I), self._on_hw))
            self.hw_combos[parte] = cb
            pf.addRow(parte, cb)
        b = QPushButton("Leer perfil")
        b.clicked.connect(lambda: self._job("hw", lambda s: s.hw(), self._on_hw))
        pf.addRow(b)
        grid.addWidget(perfil, 1, 0)

        salidas = QGroupBox("Consultas y salidas")
        sv = QVBoxLayout(salidas)
        b = QPushButton("Probar enlace con el PSoC")
        b.clicked.connect(lambda: self._job("probe", lambda s: s.probe(), None))
        sv.addWidget(b)
        b = QPushButton("Guardar evidencia JSON…")
        b.clicked.connect(self._save_evidence)
        sv.addWidget(b)
        b = QPushButton("Guardar transcript…")
        b.clicked.connect(self._save_transcript)
        sv.addWidget(b)
        b = QPushButton("Guardar todas las figuras…")
        b.clicked.connect(self._save_figures)
        sv.addWidget(b)
        b = QPushButton("Abrir captura guardada (replay, sin placa)…")
        b.clicked.connect(self._load_replay)
        sv.addWidget(b)
        sv.addStretch(1)
        grid.addWidget(salidas, 1, 1)

        crudo = QGroupBox("Comando crudo al firmware")
        ch = QHBoxLayout(crudo)
        self.raw_edit = QLineEdit()
        self.raw_edit.setPlaceholderText("lo que se escriba acá se le manda tal cual")
        self.raw_edit.returnPressed.connect(self._send_raw)
        ch.addWidget(self.raw_edit, 1)
        b = QPushButton("Mandar")
        b.clicked.connect(self._send_raw)
        ch.addWidget(b)
        grid.addWidget(crudo, 2, 0, 1, 2)

        grid.setRowStretch(3, 1)
        return w

    # -- pestaña Checklist --------------------------------------------------
    def _build_checklist_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Código", "Prueba", "Veredicto", "Detalle"])
        self.table.verticalHeader().setVisible(False)
        cab = self.table.horizontalHeader()
        cab.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        cab.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        cab.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        cab.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 300)
        v.addWidget(self.table, 1)

        self.lbl_summary = QLabel("Sin corrida todavía.")
        self.lbl_summary.setStyleSheet("font-weight: bold;")
        v.addWidget(self.lbl_summary)
        self.lbl_problems = QLabel("")
        self.lbl_problems.setWordWrap(True)
        self.lbl_problems.setStyleSheet(f"color: {figures.STATUS['FAIL']};")
        v.addWidget(self.lbl_problems)
        return w

    # -- pestaña Consola ----------------------------------------------------
    def _build_console_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        filtros = QHBoxLayout()
        self.chk_tx = QCheckBox("lo que se manda (TX)")
        self.chk_rx = QCheckBox("lo que contesta (RX)")
        self.chk_psoc = QCheckBox("eventos del PSoC")
        self.chk_info = QCheckBox("notas del banco")
        for c in (self.chk_tx, self.chk_rx, self.chk_psoc, self.chk_info):
            c.setChecked(True)
            c.stateChanged.connect(self._redraw_console)
            filtros.addWidget(c)
        self.chk_autoscroll = QCheckBox("seguir el final")
        self.chk_autoscroll.setChecked(True)
        filtros.addWidget(self.chk_autoscroll)
        filtros.addStretch(1)
        b = QPushButton("diag on")
        b.setToolTip("Enciende el eco de los eventos que el PSoC le manda al ESP.")
        b.clicked.connect(lambda: self._job("diag on", lambda s: s.diag(True), None))
        filtros.addWidget(b)
        b = QPushButton("diag off")
        b.clicked.connect(lambda: self._job("diag off", lambda s: s.diag(False), None))
        filtros.addWidget(b)
        b = QPushButton("Limpiar")
        b.clicked.connect(lambda: self.console_view.clear())
        filtros.addWidget(b)
        v.addLayout(filtros)

        self.console_view = QPlainTextEdit()
        self.console_view.setReadOnly(True)
        self.console_view.setFont(_mono())
        self.console_view.setMaximumBlockCount(20000)
        v.addWidget(self.console_view, 1)

        fila = QHBoxLayout()
        self.console_edit = QLineEdit()
        self.console_edit.setPlaceholderText("comando para el firmware (Enter manda)")
        self.console_edit.returnPressed.connect(self._send_console)
        self._history: list[str] = []
        self._history_idx = 0
        self.console_edit.installEventFilter(self)
        fila.addWidget(self.console_edit, 1)
        b = QPushButton("Mandar")
        b.clicked.connect(self._send_console)
        fila.addWidget(b)
        v.addLayout(fila)
        return w

    def eventFilter(self, obj, event):  # noqa: N802 - lo pide Qt
        """Historial con flechas en el campo de la consola."""
        from PyQt6.QtCore import QEvent

        if obj is self.console_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up and self._history:
                self._history_idx = max(0, self._history_idx - 1)
                self.console_edit.setText(self._history[self._history_idx])
                return True
            if event.key() == Qt.Key.Key_Down and self._history:
                self._history_idx = min(len(self._history), self._history_idx + 1)
                self.console_edit.setText(
                    "" if self._history_idx >= len(self._history)
                    else self._history[self._history_idx]
                )
                return True
        return super().eventFilter(obj, event)

    # -- pestaña Experimentos ----------------------------------------------
    def _build_lab_tab(self) -> QWidget:
        w = QWidget()
        split = QSplitter(Qt.Orientation.Horizontal)
        v = QVBoxLayout(w)
        v.addWidget(split)

        izq = QWidget()
        iv = QVBoxLayout(izq)

        nota = QLabel(
            "Estos comandos no necesitan el SYNC armado: miden, no capturan.\n"
            f"Cada código de IDAC vale {figures and ''}1875 µV en la referencia "
            "de esta placa."
        )
        nota.setWordWrap(True)
        nota.setStyleSheet(f"color: {figures.MUTED};")
        iv.addWidget(nota)

        gi = QGroupBox("IDAC de referencia")
        gl = QGridLayout(gi)
        self.idac_spins: dict[int, QSpinBox] = {}
        for etapa in range(4):
            gl.addWidget(QLabel(f"{etapa} · {STAGE_NAMES[etapa]}"), etapa, 0)
            sp = QSpinBox()
            sp.setRange(-255, 255)
            sp.setValue(128)
            self.idac_spins[etapa] = sp
            gl.addWidget(sp, etapa, 1)
            b = QPushButton("Fijar")
            b.clicked.connect(
                lambda _c=False, E=etapa: self._job(
                    f"idac {E}",
                    lambda s, E=E: self.worker.lab.set_idac(E, self.idac_spins[E].value()),
                    None))
            gl.addWidget(b, etapa, 2)
        iv.addWidget(gi)

        gg = QGroupBox("Ganancias")
        gf = QFormLayout(gg)
        self.gain_combos: dict[str, QComboBox] = {}
        for cual in ("pga", "pgaout"):
            cb = QComboBox()
            for i, g in enumerate(GAIN_CODES):
                cb.addItem(f"{i} — {g}x", i)
            cb.activated.connect(
                lambda idx, C=cual: self._job(
                    f"{C} {idx}",
                    lambda s, C=C, I=idx: self.worker.lab.set_gain(C, I), None))
            self.gain_combos[cual] = cb
            gf.addRow(cual, cb)
        iv.addWidget(gg)

        gm = QGroupBox("Medir")
        gmv = QVBoxLayout(gm)
        fila = QHBoxLayout()
        fila.addWidget(QLabel("canal:"))
        self.cmb_canal = QComboBox()
        for ch in range(5):
            self.cmb_canal.addItem(f"ch{ch} · {TAP_NAMES[ch]}", ch)
        fila.addWidget(self.cmb_canal, 1)
        fila.addWidget(QLabel("asentamiento:"))
        self.cmb_settle = QComboBox()
        for i, ms in enumerate(SETTLE_MS):
            self.cmb_settle.addItem(f"{i} — {ms} ms", i)
        self.cmb_settle.setCurrentIndex(3)
        fila.addWidget(self.cmb_settle, 1)
        gmv.addLayout(fila)

        b = QPushButton("Medir DC de ese canal")
        b.clicked.connect(lambda: self._job(
            "dc",
            lambda s: self.worker.lab.measure_dc(self.cmb_canal.currentData(),
                                                 self.cmb_settle.currentData()),
            self._on_dc))
        gmv.addWidget(b)
        b = QPushButton("Medir AC (RMS, pp y 50 Hz)")
        b.clicked.connect(lambda: self._job(
            "ac",
            lambda s: self.worker.lab.measure_ac(self.cmb_canal.currentData(), 0),
            self._on_ac))
        gmv.addWidget(b)
        b = QPushButton("Leer los cuatro taps")
        b.clicked.connect(lambda: self._job(
            "taps",
            lambda s: self.worker.lab.read_all_taps(self.cmb_settle.currentData()),
            self._on_taps))
        gmv.addWidget(b)
        iv.addWidget(gm)

        gs = QGroupBox("Osciloscopio lento")
        gsl = QGridLayout(gs)
        gsl.addWidget(QLabel("período [ms]"), 0, 0)
        self.spin_periodo = QSpinBox()
        self.spin_periodo.setRange(0, 5000)
        self.spin_periodo.setValue(200)
        gsl.addWidget(self.spin_periodo, 0, 1)
        gsl.addWidget(QLabel("muestras"), 1, 0)
        self.spin_n = QSpinBox()
        self.spin_n.setRange(1, 20000)
        self.spin_n.setValue(120)
        gsl.addWidget(self.spin_n, 1, 1)
        b = QPushButton("Monitorear el canal elegido")
        b.clicked.connect(self._run_monitor)
        gsl.addWidget(b, 2, 0, 1, 2)
        iv.addWidget(gs)

        gb = QGroupBox("Barrido de un IDAC")
        gbl = QGridLayout(gb)
        gbl.addWidget(QLabel("etapa"), 0, 0)
        self.cmb_sweep_stage = QComboBox()
        for e in range(4):
            self.cmb_sweep_stage.addItem(f"{e} · {STAGE_NAMES[e]}", e)
        gbl.addWidget(self.cmb_sweep_stage, 0, 1)
        gbl.addWidget(QLabel("desde / hasta / paso"), 1, 0)
        fila = QHBoxLayout()
        self.spin_lo = QSpinBox(); self.spin_lo.setRange(-255, 255); self.spin_lo.setValue(-240)
        self.spin_hi = QSpinBox(); self.spin_hi.setRange(-255, 255); self.spin_hi.setValue(240)
        self.spin_paso = QSpinBox(); self.spin_paso.setRange(1, 128); self.spin_paso.setValue(16)
        for s in (self.spin_lo, self.spin_hi, self.spin_paso):
            fila.addWidget(s)
        gbl.addLayout(fila, 1, 1)
        gbl.addWidget(QLabel("medir"), 2, 0)
        self.cmb_sweep_ch = QComboBox()
        self.cmb_sweep_ch.addItem("los cuatro taps", -1)
        for ch in range(4):
            self.cmb_sweep_ch.addItem(f"sólo ch{ch} · {TAP_NAMES[ch]}", ch)
        gbl.addWidget(self.cmb_sweep_ch, 2, 1)
        b = QPushButton("Barrer")
        b.setToolTip("Cada punto tarda 500 ms por canal: un barrido de 16 puntos "
                     "por los cuatro taps son unos 35 segundos.")
        b.clicked.connect(self._run_sweep)
        gbl.addWidget(b, 3, 0, 1, 2)
        iv.addWidget(gb)
        iv.addStretch(1)
        split.addWidget(izq)

        der = QWidget()
        dv = QVBoxLayout(der)
        self.lab_log = QPlainTextEdit()
        self.lab_log.setReadOnly(True)
        self.lab_log.setFont(_mono())
        self.lab_log.setMaximumBlockCount(5000)
        dv.addWidget(self.lab_log, 1)
        self.lab_plot = FigurePane()
        dv.addWidget(self.lab_plot, 2)
        split.addWidget(der)
        split.setSizes([380, 800])
        return w

    # -- pestaña Gráficos ---------------------------------------------------
    def _build_plots_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        fila = QHBoxLayout()
        self.cmb_fig = QComboBox()
        self.cmb_fig.addItems([
            "Matriz D2", "Diagonal D2", "Ruido D6", "Golpes D7", "Checklist",
        ])
        self.cmb_fig.currentIndexChanged.connect(self._redraw_plot)
        fila.addWidget(QLabel("Figura:"))
        fila.addWidget(self.cmb_fig, 1)
        b = QPushButton("Actualizar")
        b.clicked.connect(self._redraw_plot)
        fila.addWidget(b)
        b = QPushButton("Guardar PNG…")
        b.clicked.connect(lambda: self.plot_pane.save(self))
        fila.addWidget(b)
        v.addLayout(fila)
        self.plot_pane = FigurePane()
        v.addWidget(self.plot_pane, 1)
        return w

    # ======================================================================
    # Punto de extensión
    # ======================================================================
    def add_tab(self, widget: QWidget, title: str) -> None:
        self.tabs.addTab(widget, title)

    # ======================================================================
    # Conexión
    # ======================================================================
    def _toggle_connection(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(4000)
            self.worker = None
            self.btn_connect.setText("Conectar")
            self._set_link(None)
            return

        puerto = self.port_combo.currentData()
        if not puerto:
            QMessageBox.warning(self, "Puerto", "No hay ningún puerto elegido.")
            return
        self.worker = SerialWorker(puerto)
        self.worker.entry_received.connect(self._on_entry)
        self.worker.item_received.connect(self._on_item)
        self.worker.phase_changed.connect(self.lbl_phase.setText)
        self.worker.connection_changed.connect(self._on_connection)
        self.worker.job_started.connect(lambda n: self._set_busy(True, n))
        self.worker.job_finished.connect(lambda n, r: self._set_busy(False))
        self.worker.job_failed.connect(self._on_job_failed)
        self.worker.state_changed.connect(self._on_state)
        self.worker.start()
        self.btn_connect.setText("Desconectar")
        self.statusBar().showMessage(f"Abriendo {puerto}… (abrir resetea el ESP)")

    def _on_connection(self, ok: bool, mensaje: str) -> None:
        self.statusBar().showMessage(mensaje)
        if not ok and self.worker is not None:
            self.btn_connect.setText("Conectar")
        self._set_link(None)

    def _on_job_failed(self, nombre: str, error: str) -> None:
        self._set_busy(False)
        self.statusBar().showMessage(f"{nombre}: {error}")
        self._lab_print(f"error en {nombre}: {error}")

    def _reset_esp(self) -> None:
        def fn(s: Session):
            ok = s.console.reset_esp()
            s.note_esp_reset()
            return ok

        self._job("reset del ESP", fn, None)

    # ======================================================================
    # Trabajos
    # ======================================================================
    def _job(self, nombre: str, fn: Callable[[Session], Any],
             on_done: Optional[Callable[[Any], None]]) -> None:
        if self.worker is None or self.worker.session is None:
            QMessageBox.information(self, "Sin conexión",
                                    "Primero hay que conectarse a la placa.")
            return
        if self._busy:
            QMessageBox.information(
                self, "Ocupado",
                "Ya hay un trabajo en curso. El firmware tiene una sola consola: "
                "solaparle dos corridas da diagnósticos falsos.")
            return

        if on_done is not None:
            def una_vez(n: str, r: Any, _cb=on_done) -> None:
                try:
                    self.worker.job_finished.disconnect(una_vez)
                except TypeError:
                    pass
                _cb(r)

            self.worker.job_finished.connect(una_vez)
        self.worker.submit(nombre, fn)

    def _set_busy(self, busy: bool, nombre: str = "") -> None:
        self._busy = busy
        self.lbl_phase.setText(f"corriendo: {nombre}" if busy else "")
        if busy:
            self.statusBar().showMessage(f"{nombre}…")

    def _run_taps(self) -> None:
        n = self.spin_taps.value()
        QMessageBox.information(
            self, "Golpe al geófono",
            f"Se van a hacer {n} intentos seguidos.\n\n"
            "Golpeá el suelo al lado del geófono a ritmo parejo, uno cada dos "
            "segundos. Cada intento mide el fondo, cuenta hasta tres y captura "
            "1,47 s: no hace falta acertar ningún momento, con repetir alcanza.")
        self._job("D7", lambda s: s.tap(repeat=n), self._on_taps_result)

    def _run_monitor(self) -> None:
        ch = self.cmb_canal.currentData()
        periodo = self.spin_periodo.value()
        n = self.spin_n.value()
        self._mon_samples = []

        def fn(s: Session):
            return self.worker.lab.monitor(ch, periodo, n)

        self._lab_print(f"monitor del ch{ch} ({TAP_NAMES[ch]}): {n} muestras "
                        f"cada ~{periodo} ms")
        self._job("monitor", fn, lambda r: self._on_monitor(r, ch))

    def _run_sweep(self) -> None:
        etapa = self.cmb_sweep_stage.currentData()
        lo, hi, paso = self.spin_lo.value(), self.spin_hi.value(), self.spin_paso.value()
        ch = self.cmb_sweep_ch.currentData()
        if lo > hi:
            QMessageBox.warning(self, "Barrido", "El desde tiene que ser menor que el hasta.")
            return

        def fn(s: Session):
            return self.worker.lab.sweep(etapa, lo, hi, paso, ch)

        puntos = len(range(lo, hi + 1, paso))
        canales = 4 if ch < 0 else 1
        self._lab_print(f"barrido de {STAGE_NAMES[etapa]}: {puntos} puntos x "
                        f"{canales} canal(es), ~{puntos * canales * 0.6:.0f} s")
        self._job("barrido", fn, self._on_sweep)

    # ======================================================================
    # Slots de resultado
    # ======================================================================
    def _on_run_result(self, result: Any) -> None:
        if not isinstance(result, RunResult):
            return
        self._refresh_checklist(result.parser, result.verdict)
        self._redraw_plot()

    def _on_taps_result(self, taps: Any) -> None:
        if not isinstance(taps, list):
            return
        buenos = [t for t in taps if t.get("verdict") == "PASS"]
        self.lbl_summary.setText(
            f"D7: {len(buenos)} de {len(taps)} intentos con el golpe adentro.")
        self.cmb_fig.setCurrentIndex(3)
        self._redraw_plot()

    def _on_hw(self, hw: Any) -> None:
        if isinstance(hw, dict):
            for parte, valor in hw.items():
                cb = self.hw_combos.get(parte)
                if cb is not None and 0 <= valor <= 2:
                    cb.setCurrentIndex(valor)

    def _on_dc(self, pt: Any) -> None:
        if pt is None:
            self._lab_print("DC: sin respuesta del PSoC")
            return
        self._lab_print(f"DC ch{pt.ch} {TAP_NAMES[pt.ch]:<8} "
                        f"{pt.mean_uv / 1000.0:10.3f} mV   pp {pt.pp_uv} uV   "
                        f"({SETTLE_MS[pt.settle_sel]} ms)")

    def _on_ac(self, pt: Any) -> None:
        if pt is None:
            self._lab_print("AC: sin respuesta del PSoC")
            return
        self._lab_print(f"AC ch{pt.ch} {TAP_NAMES[pt.ch]:<8} media "
                        f"{pt.mean_uv} uV  RMS {pt.rms_uv} uV  pp {pt.pp_uv} uV  "
                        f"50 Hz {pt.hz50_uv} uV")

    def _on_taps(self, puntos: Any) -> None:
        if not isinstance(puntos, dict):
            return
        self._lab_print("--- reposo de los cuatro taps ---")
        for ch in sorted(puntos):
            self._on_dc(puntos[ch])

    def _on_monitor(self, muestras: Any, ch: int) -> None:
        if not muestras:
            self._lab_print("monitor: sin muestras")
            return
        self._mon_samples = muestras
        vals = [m.mean_uv for m in muestras]
        self._lab_print(f"monitor: {len(muestras)} muestras, "
                        f"excursión {max(vals) - min(vals)} uV")
        self.lab_plot.show_figure(figures.fig_monitor(muestras, ch=ch))

    def _on_sweep(self, sw: Any) -> None:
        if sw is None or not getattr(sw, "points", None):
            self._lab_print("barrido: sin puntos")
            return
        self._last_sweep = sw
        for ch in sorted(sw.points):
            pend = sw.slope_uv_per_code(ch)
            if pend is None:
                continue
            self._lab_print(f"barrido ch{ch} {TAP_NAMES[ch]:<8} {pend:9.1f} uV/código "
                            f"  ganancia {sw.gain_from_reference(ch):7.3f}x")
        if sw.final_code is not None:
            self._lab_print(f"el IDAC quedó en el código {sw.final_code}")
        self.lab_plot.show_figure(figures.fig_sweep(sw))

    def _on_state(self, estado: Any) -> None:
        link, sync, hw = estado
        self._set_link(link)
        self.lbl_sync.setText("SYNC: armado" if sync else "SYNC: sin armar")
        self.lbl_sync.setStyleSheet(
            f"color: {figures.STATUS['PASS'] if sync else figures.MUTED};")
        self._on_hw(hw)

    def _set_link(self, link) -> None:
        if link is None or not getattr(link, "seen", False):
            self.lbl_link.setText("enlace: ?")
            self.lbl_link.setStyleSheet(f"color: {figures.MUTED}; font-weight: bold;")
            return
        color = figures.STATUS["PASS"] if link.up else figures.STATUS["FAIL"]
        self.lbl_link.setText(
            f"enlace: {'ARRIBA' if link.up else 'CAÍDO'}  "
            f"pings {link.pings}  diag {link.diags}  malas {link.frames_bad}")
        self.lbl_link.setStyleSheet(f"color: {color}; font-weight: bold;")

    # ======================================================================
    # Checklist y consola
    # ======================================================================
    def _on_item(self, item: Item) -> None:
        fila = self.table.rowCount()
        self.table.insertRow(fila)
        for col, texto in enumerate((item.code, item.name, item.verdict, item.detail)):
            celda = QTableWidgetItem(texto)
            if col == 2:
                # El color acompaña al texto del veredicto, nunca lo reemplaza.
                celda.setForeground(QColor(figures.STATUS.get(item.verdict, figures.INK)))
                f = celda.font(); f.setBold(True); celda.setFont(f)
            self.table.setItem(fila, col, celda)
        self.table.scrollToBottom()

    def _refresh_checklist(self, parser: ChecklistParser, verdict: dict) -> None:
        self.table.setRowCount(0)
        for it in parser.items:
            self._on_item(it)
        c = verdict["counts"]
        self.lbl_summary.setText(
            f"{c['PASS']} PASS · {c['FAIL']} FAIL · {c['WARN']} WARN · "
            f"{c['SKIP']} SKIP · {c['INFO']} INFO   —   {verdict['board_verdict']}"
            f"   (cobertura {verdict['coverage']})")
        self.lbl_problems.setText("\n".join(verdict["problems"]))
        self.tabs.setCurrentIndex(1)

    def _on_entry(self, entry: con.Entry) -> None:
        if not self._entry_visible(entry):
            return
        colores = {
            con.TX: "#7a3fa0", con.RX: figures.INK,
            con.PSOC: "#1c5cab", con.INFO: figures.MUTED,
        }
        col = colores.get(entry.direction, figures.INK)
        texto = entry.format().replace("&", "&amp;").replace("<", "&lt;")
        self.console_view.appendHtml(
            f'<span style="color:{col};white-space:pre">{texto}</span>')
        if self.chk_autoscroll.isChecked():
            self.console_view.moveCursor(QTextCursor.MoveOperation.End)

    def _entry_visible(self, entry: con.Entry) -> bool:
        return {
            con.TX: self.chk_tx.isChecked(),
            con.RX: self.chk_rx.isChecked(),
            con.PSOC: self.chk_psoc.isChecked(),
            con.INFO: self.chk_info.isChecked(),
        }.get(entry.direction, True)

    def _redraw_console(self) -> None:
        self.console_view.clear()
        if self.worker is None or self.worker.console is None:
            return
        for e in self.worker.console.transcript:
            self._on_entry(e)

    def _send_console(self) -> None:
        texto = self.console_edit.text().strip()
        if not texto:
            return
        self._history.append(texto)
        self._history_idx = len(self._history)
        self.console_edit.clear()
        self._job(texto, lambda s, T=texto: s.raw(T), None)

    def _send_raw(self) -> None:
        texto = self.raw_edit.text().strip()
        if not texto:
            return
        self.raw_edit.clear()
        self._job(texto, lambda s, T=texto: s.raw(T), None)

    def _lab_print(self, texto: str) -> None:
        self.lab_log.appendPlainText(texto)

    # ======================================================================
    # Figuras y archivos
    # ======================================================================
    def _current_parser(self) -> ChecklistParser:
        if self.worker is not None and self.worker.session is not None:
            if self.worker.session.parser.items:
                return self.worker.session.parser
        return self.offline_parser

    def _redraw_plot(self) -> None:
        p = self._current_parser()
        m = p.meas
        idx = self.cmb_fig.currentIndex()
        try:
            if idx == 0:
                if not any(v is not None for fila in m.d2 for v in fila):
                    self.plot_pane.show_message("Todavía no corrió D2.")
                    return
                self.plot_pane.show_figure(figures.fig_d2_matrix(m))
            elif idx == 1:
                if not any(v is not None for v in m.d2_diagonal()):
                    self.plot_pane.show_message("Todavía no corrió D2.")
                    return
                self.plot_pane.show_figure(figures.fig_d2_diagonal(m))
            elif idx == 2:
                if not m.d6:
                    self.plot_pane.show_message("Todavía no corrió D6.")
                    return
                self.plot_pane.show_figure(figures.fig_d6(m))
            elif idx == 3:
                if not m.d7:
                    self.plot_pane.show_message("Todavía no hubo intentos de D7.")
                    return
                self.plot_pane.show_figure(figures.fig_d7(m.d7))
            else:
                if not p.items:
                    self.plot_pane.show_message("Todavía no hay checklist.")
                    return
                self.plot_pane.show_figure(figures.fig_checklist(p.items, evaluate(p)))
        except Exception as exc:
            self.plot_pane.show_message(f"No se pudo dibujar: {exc}")

    def _load_replay(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Abrir captura guardada", "", "Texto (*.log *.txt);;Todos (*)")
        if not ruta:
            return
        texto = Path(ruta).read_text(encoding="utf-8", errors="replace")
        self.load_report(texto)
        self.statusBar().showMessage(f"Replay de {Path(ruta).name}")

    def load_report(self, texto: str) -> None:
        """Carga una corrida desde texto. Es la ruta que usa el replay y el smoke."""
        p = ChecklistParser()
        p.feed_many(texto)
        self.offline_parser = p
        self._refresh_checklist(p, evaluate(p))
        self._redraw_plot()

    def _save_evidence(self) -> None:
        from .core import evidence

        p = self._current_parser()
        if not p.items:
            QMessageBox.information(self, "Evidencia", "No hay ninguna corrida cargada.")
            return
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar evidencia",
                                              "corrida.json", "JSON (*.json)")
        if not ruta:
            return
        r = RunResult("gui", p, evaluate(p), 0.0, p.run_finished)
        puerto = self.port_combo.currentData() or ""
        evidence.save(evidence.build(r, port=puerto, started_at=evidence.utc_now(),
                                     hw_profile=dict(p.hw)), ruta)
        self.statusBar().showMessage(f"Evidencia en {ruta}")

    def _save_transcript(self) -> None:
        from .core import evidence

        if self.worker is None or self.worker.console is None:
            QMessageBox.information(self, "Transcript", "No hay sesión abierta.")
            return
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar transcript",
                                              "transcript.txt", "Texto (*.txt)")
        if ruta:
            evidence.save_transcript(self.worker.console.transcript.text(), ruta)
            self.statusBar().showMessage(f"Transcript en {ruta}")

    def _save_figures(self) -> None:
        p = self._current_parser()
        if not p.items:
            QMessageBox.information(self, "Figuras", "No hay ninguna corrida cargada.")
            return
        carpeta = QFileDialog.getExistingDirectory(self, "Carpeta para las figuras")
        if not carpeta:
            return
        rutas = figures.save_all(carpeta, p.items, evaluate(p), p.meas)
        self.statusBar().showMessage(f"{len(rutas)} figuras en {carpeta}")

    # ======================================================================
    def closeEvent(self, event):  # noqa: N802 - lo pide Qt
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(3000)
        event.accept()


# ==========================================================================
# Entrada y smoke test
# ==========================================================================
def _smoke() -> int:
    """Prueba sin ventana ni placa. Construye todo y verifica que se dibuje."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from .core.checklist import GOOD_REPORT, REAL_DETAILS

    app = QApplication.instance() or QApplication([])
    win = MainWindow()

    fallas: list[str] = []

    def check(nombre: str, cond: bool) -> None:
        print(f"[{'PASS' if cond else 'FAIL'}] {nombre}")
        if not cond:
            fallas.append(nombre)

    win.load_report(GOOD_REPORT)
    check("checklist con 16 filas", win.table.rowCount() == 16)
    check("resumen escrito", "APTO" in win.lbl_summary.text())

    # Con los detalles reales se puede dibujar todo.
    win.load_report(GOOD_REPORT + REAL_DETAILS)
    for i, nombre in enumerate(["matriz D2", "diagonal D2", "ruido D6",
                                "golpes D7", "checklist"]):
        win.cmb_fig.setCurrentIndex(i)
        win._redraw_plot()
        check(f"figura: {nombre}", win.plot_pane._figure is not None)

    # Las figuras del modo manual.
    from .core.lab import MonSample, Sweep

    muestras = [MonSample(i, i * 137, 3, 941800 + (i % 5) * 60, 1200, True)
                for i in range(40)]
    win.lab_plot.show_figure(figures.fig_monitor(muestras, ch=3))
    check("figura: monitor", win.lab_plot._figure is not None)

    sw = Sweep(stage=3)
    for code in range(0, 241, 40):
        sw.add(3, code, 745000 + 1240 * code)
    sw.final_code = 240
    win.lab_plot.show_figure(figures.fig_sweep(sw))
    check("figura: barrido", win.lab_plot._figure is not None)
    check("barrido: pendiente ajustada",
          abs(sw.slope_uv_per_code(3) - 1240.0) < 1.0)

    check("add_tab disponible", callable(getattr(win, "add_tab", None)))
    check("las cinco pestañas", win.tabs.count() == 5)
    check("sin conexion no crashea", win._current_parser() is win.offline_parser)

    win.close()
    print(f"\ngui smoke: {'OK' if not fallas else 'FALLAS: ' + ', '.join(fallas)}")
    return 0 if not fallas else 1


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="testbench gui", description="Banco, modo gráfico")
    p.add_argument("--port", default=None, help="COM del ESP; si se omite se detecta")
    p.add_argument("--smoke", action="store_true",
                   help="prueba headless sin ventana ni placa")
    args = p.parse_args(argv)

    if args.smoke:
        return _smoke()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Banco de placas")
    win = MainWindow(port=args.port or con.default_port())
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
