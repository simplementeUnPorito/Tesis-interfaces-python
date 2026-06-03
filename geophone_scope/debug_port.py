"""debug_port.py — QThread for reading slave/master debug serial ports (text lines)."""

from __future__ import annotations

import time
from typing import Optional

import serial
from PyQt6.QtCore import QThread, pyqtSignal

import config


class DebugPortWorker(QThread):
    """
    Reads a secondary serial port (slave or master debug UART) and emits text lines.

    These ports carry human-readable debug output from the ESP32 via Serial1/GPIO.
    They are independent of the binary data stream on the main USB COM port.
    """

    # Emitted with each decoded text line received from the debug port
    line_received: pyqtSignal = pyqtSignal(str)

    # Emitted when the port opens (True) or closes/errors (False)
    connection_changed: pyqtSignal = pyqtSignal(bool)

    # Text-level log events
    log_message: pyqtSignal = pyqtSignal(str)

    def __init__(
        self,
        port: str,
        baud: int = 115_200,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._port_name: str    = port
        self._baud:      int    = baud
        self._running:   bool   = False
        self._ser:       Optional[serial.Serial] = None
        self._line_buf:  str    = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Request thread termination."""
        self._running = False

    @property
    def port_name(self) -> str:
        return self._port_name

    # ── QThread entry point ──────────────────────────────────────────────────

    def run(self) -> None:
        """Open the debug port, read text lines, emit them, handle errors."""
        self._running = True
        self._line_buf = ""

        try:
            self._ser = serial.Serial(
                port=self._port_name,
                baudrate=self._baud,
                timeout=0,
                rtscts=False,
                dsrdtr=False,
            )
            self._ser.setDTR(False)
            self._ser.setRTS(False)
        except Exception as exc:
            self.log_message.emit(f"DebugPort: cannot open {self._port_name}: {exc}")
            self.connection_changed.emit(False)
            return

        self.connection_changed.emit(True)
        self.log_message.emit(f"DebugPort: connected to {self._port_name} @ {self._baud}")

        while self._running:
            try:
                if self._ser.in_waiting > 0:
                    raw = self._ser.read(self._ser.in_waiting)
                    text = raw.decode("utf-8", errors="replace")
                    self._line_buf += text
                    # Emit complete lines
                    while "\n" in self._line_buf:
                        line, self._line_buf = self._line_buf.split("\n", 1)
                        self.line_received.emit(line.rstrip("\r"))
            except serial.SerialException as exc:
                self.log_message.emit(f"DebugPort RX error: {exc}")
                self.connection_changed.emit(False)
                break
            except Exception as exc:
                self.log_message.emit(f"DebugPort unexpected: {exc}")
                break

            time.sleep(0.05)

        self._close_port()
        self.log_message.emit(f"DebugPort: {self._port_name} closed")

    # ── Private ───────────────────────────────────────────────────────────────

    def _close_port(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
