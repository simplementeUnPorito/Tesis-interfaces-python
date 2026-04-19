"""
serial_worker.py — Background USB CDC reader thread.

Parses three packet types from the same COM port:
  0xAA — data sample (12 bytes)     → data_ready(list)
  0xCC — state/config (10 bytes)    → state_update(dict)
  0xDD — debug event (4+ bytes)     → debug_event(dict)

Auto-detects the PSoC USB CDC port by Cypress VID (0x04B4).
All activity is written to the shared AppLogger.
"""

import serial
import serial.tools.list_ports
from PyQt5.QtCore import QThread, pyqtSignal

from protocol      import (PKT_DATA_HEADER, PKT_DATA_SIZE, parse_packet)
from debug_protocol import (DBG_HEADER, STATE_HEADER, STATE_PKT_LEN,
                             parse_debug_packet, parse_state_packet)
from app_logger import log

CYPRESS_VID = 0x04B4


class SerialWorker(QThread):
    data_ready   = pyqtSignal(list)    # list of parsed data packet dicts
    state_update = pyqtSignal(dict)    # parsed state packet dict
    debug_event  = pyqtSignal(dict)    # parsed debug event dict
    connected    = pyqtSignal()
    disconnected = pyqtSignal()
    error        = pyqtSignal(str)

    BATCH_SIZE = 30

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
        buf   = bytearray()
        batch = []

        log(f"[USB] Abriendo {self._port} @ {self._baudrate}")
        try:
            self._ser = serial.Serial(
                self._port, self._baudrate,
                timeout=0.05,
                write_timeout=1.0
            )
            log(f"[USB] Puerto abierto OK — {self._port}")
            self.connected.emit()
        except serial.SerialException as e:
            msg = f"No se pudo abrir {self._port}: {e}"
            log(f"[USB] ERROR: {msg}")
            self.error.emit(msg)
            self._running = False
            return

        garbage_count = 0
        total_bytes   = 0
        pkt_data      = 0
        pkt_state     = 0
        pkt_dbg       = 0
        pkt_bad       = 0

        while self._running:
            try:
                chunk = self._ser.read(256)
            except serial.SerialException as e:
                log(f"[USB] Serial read error: {e}")
                self.error.emit(f"Serial read error: {e}")
                break

            if not chunk:
                continue

            total_bytes += len(chunk)
            # Log first raw bytes received so we can see the protocol
            if total_bytes <= 128:
                log(f"[USB] RAW({len(chunk)}B): {chunk.hex()}")

            buf.extend(chunk)

            while buf:
                if buf[0] == PKT_DATA_HEADER:
                    if len(buf) < PKT_DATA_SIZE:
                        break
                    pkt = parse_packet(bytes(buf[:PKT_DATA_SIZE]))
                    if pkt is not None:
                        batch.append(pkt)
                        pkt_data += 1
                        del buf[:PKT_DATA_SIZE]
                    else:
                        raw = bytes(buf[:PKT_DATA_SIZE]).hex()
                        log(f"[USB] 0xAA pkt CRC FAIL: {raw}")
                        pkt_bad += 1
                        del buf[:1]

                elif buf[0] == STATE_HEADER:
                    if len(buf) < STATE_PKT_LEN:
                        break
                    st = parse_state_packet(bytes(buf[:STATE_PKT_LEN]))
                    if st is not None:
                        log(f"[USB] STATE pkt: {st}")
                        self.state_update.emit(st)
                        pkt_state += 1
                        del buf[:STATE_PKT_LEN]
                    else:
                        raw = bytes(buf[:STATE_PKT_LEN]).hex()
                        log(f"[USB] 0xCC pkt CRC FAIL: {raw}")
                        pkt_bad += 1
                        del buf[:1]

                elif buf[0] == DBG_HEADER:
                    if len(buf) < 4:
                        break
                    plen     = buf[2]
                    pkt_size = 4 + plen
                    if len(buf) < pkt_size:
                        break
                    dbg = parse_debug_packet(bytes(buf[:pkt_size]))
                    if dbg is not None:
                        log(f"[USB] DBG evt: {dbg['label']} — {dbg['detail']}")
                        self.debug_event.emit(dbg)
                        pkt_dbg += 1
                        del buf[:pkt_size]
                    else:
                        raw = bytes(buf[:pkt_size]).hex()
                        log(f"[USB] 0xDD pkt CRC FAIL: {raw}")
                        pkt_bad += 1
                        del buf[:1]

                else:
                    if garbage_count < 20:
                        log(f"[USB] GARBAGE byte: 0x{buf[0]:02X} (total_rx={total_bytes})")
                    elif garbage_count == 20:
                        log("[USB] GARBAGE: suprimiendo logs adicionales de bytes sueltos")
                    garbage_count += 1
                    del buf[:1]

            if len(batch) >= self.BATCH_SIZE:
                log(f"[USB] batch data_ready: {len(batch)} pkts | total data={pkt_data} state={pkt_state} dbg={pkt_dbg} bad={pkt_bad} garbage={garbage_count}")
                self.data_ready.emit(batch)
                batch = []

        if batch:
            self.data_ready.emit(batch)

        log(f"[USB] Sesión terminada — total_bytes={total_bytes} data={pkt_data} state={pkt_state} dbg={pkt_dbg} bad={pkt_bad} garbage={garbage_count}")
        if self._ser and self._ser.is_open:
            self._ser.close()
        self.disconnected.emit()

    def send(self, data: bytes):
        if self._ser and self._ser.is_open:
            try:
                log(f"[USB] TX: {data.hex()}")
                self._ser.write(data)
            except serial.SerialException as e:
                log(f"[USB] TX ERROR: {e}")

    def stop(self):
        self._running = False
        self.wait(2000)

    # ── Static utilities ──────────────────────────────────────────────────────

    @staticmethod
    def detect_psoc_port() -> str | None:
        """Return the first COM port with Cypress VID (0x04B4), or None."""
        for p in serial.tools.list_ports.comports():
            if getattr(p, 'vid', None) == CYPRESS_VID:
                return p.device
        return None

    @staticmethod
    def list_ports() -> list[str]:
        """Return all available COM port names with descriptions."""
        ports = serial.tools.list_ports.comports()
        result = []
        for p in ports:
            label = p.device
            if p.description and p.description != "n/a":
                label = f"{p.device}  ({p.description})"
            result.append(label)
        return result

    @staticmethod
    def port_names() -> list[str]:
        """Return bare port names (e.g. 'COM3') for use in Serial.Serial()."""
        return [p.device for p in serial.tools.list_ports.comports()]
