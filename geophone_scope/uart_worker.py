"""
uart_worker.py — Background UART reader for PSoC debug events.

Reads binary debug packets [0xDD]... from the UART_PC serial port and
emits them as parsed dicts via the debug_event signal.
"""

import serial
import serial.tools.list_ports
from PyQt5.QtCore import QThread, pyqtSignal

from debug_protocol import DBG_HEADER, parse_debug_packet


class UartWorker(QThread):
    debug_event  = pyqtSignal(dict)   # parsed debug event
    connected    = pyqtSignal()
    disconnected = pyqtSignal()
    error        = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._port     = None
        self._baudrate = 115200
        self._running  = False
        self._ser      = None

    def set_port(self, port: str, baudrate: int = 115200):
        self._port     = port
        self._baudrate = baudrate

    def run(self):
        self._running = True
        buf = bytearray()

        try:
            self._ser = serial.Serial(
                self._port, self._baudrate,
                timeout=0.05,
                write_timeout=1.0
            )
            self.connected.emit()
        except serial.SerialException as e:
            self.error.emit(f"UART: no se pudo abrir {self._port}: {e}")
            self._running = False
            return

        while self._running:
            try:
                chunk = self._ser.read(256)
            except serial.SerialException as e:
                self.error.emit(f"UART read error: {e}")
                break

            if not chunk:
                continue

            buf.extend(chunk)
            self._parse_buf(buf)

        if self._ser and self._ser.is_open:
            self._ser.close()
        self.disconnected.emit()

    def _parse_buf(self, buf: bytearray):
        while len(buf) >= 4:
            idx = buf.find(DBG_HEADER)
            if idx == -1:
                buf.clear()
                return
            if idx > 0:
                del buf[:idx]

            if len(buf) < 4:
                return

            plen = buf[2]
            pkt_size = 4 + plen   # header + event + len + payload + crc

            if len(buf) < pkt_size:
                return   # wait for more bytes

            pkt = parse_debug_packet(bytes(buf[:pkt_size]))
            if pkt is not None:
                self.debug_event.emit(pkt)
                del buf[:pkt_size]
            else:
                del buf[:1]   # bad CRC: skip and retry

    def stop(self):
        self._running = False
        self.wait(2000)

    @staticmethod
    def list_ports() -> list[str]:
        return [p.device for p in serial.tools.list_ports.comports()]
