"""serial_worker.py — QThread that owns the serial port and emits parsed packets."""

from __future__ import annotations

import queue
import time
from typing import Optional

import serial
from PyQt6.QtCore import QThread, pyqtSignal

import config
from protocol import Packet, encode_std, find_and_decode


class SerialWorker(QThread):
    """
    Background thread that reads the master serial port and emits Packet signals.

    All serial I/O happens here; the GUI thread only calls send() or stop().
    Reconnection is handled automatically with a 1-second back-off.
    """

    # Emitted for every successfully parsed packet
    packet_received: pyqtSignal = pyqtSignal(object)

    # Emitted when the connection state changes (True=connected, False=lost)
    connection_changed: pyqtSignal = pyqtSignal(bool)

    # Emitted for debug/status messages that should appear in the GUI log
    log_message: pyqtSignal = pyqtSignal(str)

    def __init__(
        self,
        port: str,
        baud: int = config.BAUD,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._port_name: str   = port
        self._baud:      int   = baud
        self._running:   bool  = False
        self._ser:       Optional[serial.Serial] = None
        self._tx_queue:  queue.SimpleQueue[bytes] = queue.SimpleQueue()
        self._rx_buf:    bytearray = bytearray()
        self._probe_packets: list[Packet] = []

    # ── Public API (called from the GUI thread) ───────────────────────────────

    def send(self, data: bytes) -> None:
        """Queue bytes to be written to serial in the worker thread (thread-safe)."""
        self._tx_queue.put(data)

    def stop(self) -> None:
        """Request the thread to stop and close the port."""
        self._running = False

    @property
    def port_name(self) -> str:
        return self._port_name

    # ── QThread entry point ──────────────────────────────────────────────────

    def run(self) -> None:
        """Main loop: open port, read/write, emit signals, handle errors."""
        self._running = True
        self._rx_buf  = bytearray()

        try:
            self._open_port()
        except Exception as exc:
            self.log_message.emit(f"SerialWorker: cannot open {self._port_name}: {exc}")
            self.connection_changed.emit(False)
            return

        self.connection_changed.emit(True)
        self.log_message.emit(f"SerialWorker: connected to {self._port_name} @ {self._baud}")
        for pkt in self._probe_packets:
            self.packet_received.emit(pkt)
        self._probe_packets = []

        while self._running:
            # ── Transmit pending bytes ────────────────────────────────────────
            while not self._tx_queue.empty():
                try:
                    data = self._tx_queue.get_nowait()
                    if self._ser and self._ser.is_open:
                        self._ser.write(data)
                except Exception as exc:
                    self.log_message.emit(f"SerialWorker TX error: {exc}")
                    break

            # ── Read available bytes ──────────────────────────────────────────
            try:
                if self._ser and self._ser.is_open:
                    waiting = self._ser.in_waiting
                    if waiting > 0:
                        chunk = self._ser.read(min(waiting, 4096))
                        if chunk:
                            self._rx_buf.extend(chunk)
                            self._parse_buffer()
            except serial.SerialException as exc:
                self.log_message.emit(f"SerialWorker RX error: {exc}")
                self.connection_changed.emit(False)
                self._close_port()
                # Back-off before signalling final failure
                time.sleep(1.0)
                break
            except Exception as exc:
                self.log_message.emit(f"SerialWorker unexpected error: {exc}")
                break

            # Yield to OS without blocking the Qt event loop
            time.sleep(config.SERIAL_POLL_MS / 1000.0)

        self._close_port()
        self.log_message.emit("SerialWorker: stopped")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _open_port(self) -> None:
        self._ser = serial.Serial(
            port=self._port_name,
            baudrate=self._baud,
            timeout=0,          # non-blocking read
            write_timeout=1.0,
        )
        try:
            self._ser.setDTR(False)
            self._ser.setRTS(False)
            self._ser.reset_input_buffer()
        except Exception:
            pass
        if not self._wait_for_master_protocol():
            self._close_port()
            raise RuntimeError("no responde como master de datos (0x56); probablemente es un COM de logs")

        # Send an initial STOP to bring master back to IDLE.
        try:
            self._ser.write(encode_std(config.CMD_STOP, 0))
        except Exception:
            pass

    def _wait_for_master_protocol(self) -> bool:
        """Accept only the master data port, identified by 0x56 binary packets."""
        if self._ser is None:
            return False

        deadline = time.monotonic() + 4.0
        next_probe = 0.0
        probe_buf = bytearray()
        while self._running and time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_probe:
                try:
                    self._ser.write(encode_std(config.CMD_STATUS, 0))
                except Exception:
                    return False
                next_probe = now + 0.35

            try:
                waiting = self._ser.in_waiting
                if waiting > 0:
                    probe_buf.extend(self._ser.read(min(waiting, 4096)))
                    packets, probe_buf = find_and_decode(probe_buf)
                    valid_packets = [
                        pkt for pkt in packets
                        if self._is_master_probe_packet(pkt)
                    ]
                    if valid_packets:
                        self._probe_packets.extend(valid_packets)
                        self._rx_buf = probe_buf
                        return True
            except Exception:
                return False

            time.sleep(0.02)
        return False

    @staticmethod
    def _is_master_probe_packet(pkt: Packet) -> bool:
        if pkt.node_id != config.MASTER_NODE_ID:
            return False
        if pkt.ptype == config.PTYPE_READY:
            return 0 <= pkt.ready_n_slaves < config.MAX_NODES
        if pkt.ptype == config.PTYPE_STATUS:
            return pkt.status_espnow_ok in (0, 1)
        return False

    def _close_port(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def _parse_buffer(self) -> None:
        """Extract all complete 6-byte packets from _rx_buf and emit them."""
        packets, self._rx_buf = find_and_decode(self._rx_buf)
        for pkt in packets:
            self.packet_received.emit(pkt)
