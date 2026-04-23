"""scope_min.py — Scope PSoC5 Geophone Filter Testbench

Protocolo RX (PSoC -> PC, 10 bytes, 9600 baud):
    [0xAA][0x00][sar_hi][sar_lo][ds2][ds1][ds0][flt2][flt1][flt0]
    sar = int16 big-endian
    ds  = int24 big-endian signed
    flt = int24 big-endian signed

Protocolo TX (PC -> PSoC):
    [0xBB][0x03][fhi][flo]  — set freq VDAC (Hz*10, uint16 big-endian)

Uso:
    python scope_min.py
    python scope_min.py COM7
"""

import sys
import os
import struct
import threading
import collections
import logging
import time
import queue
from datetime import datetime

import serial
import serial.tools.list_ports
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout,
    QDoubleSpinBox, QLabel, QGroupBox,
)
from PyQt5.QtCore import QTimer, pyqtSignal, QObject, Qt
import pyqtgraph as pg
import numpy as np

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(_DIR, "logs"), exist_ok=True)
_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(os.path.join(_DIR, "logs", f"scope_{_ts}.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scope")

# ---------------------------------------------------------------------------
# Constantes de protocolo
# ---------------------------------------------------------------------------
BAUD       = 9600
PKT_HEADER = 0xAA
PKT_LEN    = 10
CMD_MARKER = 0xBB
CMD_FREQ   = 0x03
CYPRESS_VID = 0x04B4
PLOT_LEN   = 1500    # muestras en pantalla (~0.5 s a 3000 Hz)

# ---------------------------------------------------------------------------
# Señales Qt (emitidas desde el hilo serial hacia el hilo GUI)
# ---------------------------------------------------------------------------
class _Signals(QObject):
    packet = pyqtSignal(int, int, int)   # sar, ds, flt


# ---------------------------------------------------------------------------
# Hilo serial — parser por máquina de estados explícita
# ---------------------------------------------------------------------------
class SerialWorker(threading.Thread):
    """
    Estados del parser:
      HUNT    — descarta bytes hasta encontrar 0xAA
      COLLECT — acumula PKT_LEN-1 bytes restantes del paquete
    """

    _ST_HUNT    = 0
    _ST_COLLECT = 1

    def __init__(self, port: str):
        super().__init__(daemon=True, name="SerialWorker")
        self.port    = port
        self.signals = _Signals()
        self._ser    = None
        self._stop   = threading.Event()
        self._txq    = queue.Queue()

        self._pkt_count  = 0
        self._byte_count = 0

    # ------------------------------------------------------------------
    def run(self):
        if not self._open_port():
            return

        state = self._ST_HUNT
        buf   = bytearray()          # paquete en construcción en COLLECT

        log.info("[serial] Entrando en loop de lectura")
        while not self._stop.is_set():

            # Drenar cola TX en el mismo hilo
            while not self._txq.empty():
                try:
                    data, desc = self._txq.get_nowait()
                    self._ser.write(data)
                    log.info(f"[serial] TX {desc}: {data.hex()}")
                except queue.Empty:
                    break
                except serial.SerialException as e:
                    log.error(f"[serial] TX error: {e}")

            # Leer hasta 64 bytes (timeout=0.1 s configurado en Serial)
            try:
                chunk = self._ser.read(64)
            except serial.SerialException as e:
                log.error(f"[serial] Error de lectura: {e}")
                break

            if not chunk:
                continue

            self._byte_count += len(chunk)
            log.debug(f"[serial] RX {len(chunk)} bytes: {chunk.hex()}")

            for b in chunk:
                if state == self._ST_HUNT:
                    if b == PKT_HEADER:
                        buf   = bytearray([b])
                        state = self._ST_COLLECT
                else:  # COLLECT
                    buf.append(b)
                    if len(buf) == PKT_LEN:
                        self._parse(buf)
                        buf   = bytearray()
                        state = self._ST_HUNT

        log.info("[serial] Loop terminado")
        if self._ser and self._ser.is_open:
            self._ser.close()
            log.info("[serial] Puerto cerrado")

    # ------------------------------------------------------------------
    def _open_port(self) -> bool:
        for attempt in range(1, 9):
            try:
                self._ser = serial.Serial(
                    self.port,
                    baudrate=BAUD,
                    timeout=0.1,
                    write_timeout=1.0,
                    dsrdtr=False,
                    rtscts=False,
                )
                log.info(f"[serial] Puerto abierto en intento {attempt}: {self._ser.name}")
                return True
            except serial.SerialException as e:
                log.warning(f"[serial] Intento {attempt}/8 fallido: {e}")
                if attempt < 8:
                    time.sleep(0.5)
        log.error(f"[serial] No se pudo abrir {self.port} tras 8 intentos")
        return False

    # ------------------------------------------------------------------
    def _parse(self, pkt: bytearray):
        # [AA][00][sar_hi][sar_lo][ds2][ds1][ds0][flt2][flt1][flt0]
        sar = struct.unpack('>h', bytes(pkt[2:4]))[0]
        ds  = int.from_bytes(bytes(pkt[4:7]), 'big', signed=True)
        flt = int.from_bytes(bytes(pkt[7:10]), 'big', signed=True)
        self._pkt_count += 1
        if self._pkt_count <= 5 or self._pkt_count % 1000 == 0:
            log.debug(f"[serial] PKT #{self._pkt_count}: sar={sar} ds={ds} flt={flt}")
        self.signals.packet.emit(sar, ds, flt)

    # ------------------------------------------------------------------
    def send(self, data: bytes, desc: str = ""):
        if self._ser and self._ser.is_open:
            self._txq.put((data, desc))
        else:
            log.warning(f"[serial] TX ignorado (puerto cerrado): {desc}")

    def stop(self):
        self._stop.set()


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------
class ScopeWindow(QMainWindow):
    def __init__(self, port: str):
        super().__init__()
        self.setWindowTitle("Geophone Scope")
        self.resize(1000, 750)

        self._bufs = {
            'sar': collections.deque([0.0] * PLOT_LEN, maxlen=PLOT_LEN),
            'ds':  collections.deque([0.0] * PLOT_LEN, maxlen=PLOT_LEN),
            'flt': collections.deque([0.0] * PLOT_LEN, maxlen=PLOT_LEN),
        }

        self._build_ui()
        self._worker = SerialWorker(port)
        self._worker.signals.packet.connect(self._on_packet, Qt.QueuedConnection)
        self._worker.start()
        self._lbl.setText(f"Puerto: {port}")

        self._timer = QTimer()
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        log.info(f"[GUI] ScopeWindow lista, puerto={port}")

    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Barra de control
        ctrl = QHBoxLayout()
        g = QGroupBox("Freq VDAC (Hz)")
        gl = QVBoxLayout(g)
        self._sb = QDoubleSpinBox()
        self._sb.setRange(1.0, 300.0)
        self._sb.setSingleStep(1.0)
        self._sb.setDecimals(1)
        self._sb.setValue(1.0)
        self._sb.editingFinished.connect(self._on_freq)
        gl.addWidget(self._sb)
        ctrl.addWidget(g)
        self._lbl = QLabel("—")
        ctrl.addWidget(self._lbl)
        ctrl.addStretch()
        root.addLayout(ctrl)

        # Plots
        pg.setConfigOption('background', 'k')
        pg.setConfigOption('foreground', 'w')
        x = np.arange(PLOT_LEN)
        specs = [
            ('sar', 'y', 'SAR — entrada (int16)',       (-2048,   2047)),
            ('ds',  'c', 'ADC DelSig — crudo (int24)',  (-131072, 131071)),
            ('flt', 'm', 'Filter DFB — salida (int24)', (-8388608, 8388607)),
        ]
        self._curves = {}
        for key, color, title, ylim in specs:
            pw = pg.PlotWidget(title=title)
            pw.setYRange(*ylim)
            pw.showGrid(x=True, y=True, alpha=0.3)
            self._curves[key] = pw.plot(x, np.zeros(PLOT_LEN), pen=color)
            root.addWidget(pw)

    # ------------------------------------------------------------------
    def _on_packet(self, sar: int, ds: int, flt: int):
        self._bufs['sar'].append(float(sar))
        self._bufs['ds'].append(float(ds))
        self._bufs['flt'].append(float(flt))

    def _refresh(self):
        x = np.arange(PLOT_LEN)
        for key, curve in self._curves.items():
            curve.setData(x, np.array(self._bufs[key]))

    def _on_freq(self):
        freq_hz = self._sb.value()
        freq_dh = max(1, int(round(freq_hz * 10)))
        hi = (freq_dh >> 8) & 0xFF
        lo =  freq_dh       & 0xFF
        cmd = bytes([CMD_MARKER, CMD_FREQ, hi, lo])
        self._worker.send(cmd, f"CMD_FREQ {freq_hz}Hz (dh={freq_dh})")
        log.info(f"[GUI] Freq → {freq_hz} Hz, bytes={cmd.hex()}")

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        log.info("[GUI] Cerrando...")
        try:
            self._sb.editingFinished.disconnect(self._on_freq)
        except RuntimeError:
            pass
        self._timer.stop()
        self._worker.stop()
        self._worker.join(timeout=3.0)
        log.info("[GUI] Worker terminado")
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Detección automática del puerto Cypress
# ---------------------------------------------------------------------------
def find_cypress_port() -> str | None:
    log.info("[USB] Buscando dispositivo Cypress (VID=0x04B4)...")
    for p in serial.tools.list_ports.comports():
        vid = f"{p.vid:#06x}" if p.vid else "None"
        log.info(f"[USB]   {p.device} VID={vid} — {p.description}")
        if p.vid == CYPRESS_VID:
            log.info(f"[USB] Encontrado: {p.device}")
            return p.device
    log.warning("[USB] No se encontro dispositivo Cypress")
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log.info("=" * 60)
    log.info("scope_min.py iniciando")
    log.info("=" * 60)

    if len(sys.argv) > 1:
        port = sys.argv[1]
        log.info(f"Puerto por argumento: {port}")
    else:
        port = find_cypress_port()
        if port is None:
            print("No se encontro dispositivo Cypress (VID=0x04B4).")
            print("Uso: python scope_min.py COM7")
            sys.exit(1)

    app = QApplication(sys.argv)
    win = ScopeWindow(port)
    win.show()
    sys.exit(app.exec_())
