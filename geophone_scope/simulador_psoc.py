#!/usr/bin/env python3
"""
simulador_psoc.py — Emulador virtual del PSoC para pruebas del osciloscopio.

Crea un par de puertos serie virtuales con os.openpty(). El osciloscopio
conecta al path del slave (impreso en stdout). El simulador genera 3 señales
sintéticas y emite paquetes 0xAA a 1500 Hz; responde a comandos con
paquetes 0xCC de estado.

Uso:
    python simulador_psoc.py
    # Anotar el path /dev/pts/X y conectar el osciloscopio a ese puerto
"""

import os
import math
import time
import struct
import threading
import sys
import termios
import tty

SAMPLE_RATE = 1500      # Hz - igual que el PSoC real

# Cabeceras de paquete
PKT_DATA_HEADER = 0xAA
STATE_HEADER    = 0xCC
DBG_HEADER      = 0xDD
CMD_MARKER      = 0xBB

# Comandos (igual que Communication.h)
CMD_START      = 0x01
CMD_STOP       = 0x02
CMD_SET_MUX_IN = 0x03
CMD_SET_MUX_OUT= 0x04
CMD_SET_ADC    = 0x05
CMD_VDAC_LOAD  = 0x06
CMD_SET_DEBUG  = 0x07
CMD_SET_DBG_CH = 0x08
CMD_GET_STATE  = 0x09
CMD_LOAD_FIR   = 0x0A
CMD_FIR_CLEAR  = 0x0B

# Tamaños de paquete
PKT_DATA_SIZE  = 12
STATE_PKT_SIZE = 10
DBG_HDR_SIZE   = 4


def _crc_xor(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc


class PSoCSimulator:
    def __init__(self, master_fd: int):
        self.master_fd = master_fd

        # Estado del PSoC simulado
        self.streaming  = False
        self.adc_cfg    = 4
        self.buf_gain   = 8
        self.vdac_mode  = 0
        self.filter_sel = 0
        self.debug_en   = 0
        self.debug_ch   = 0

        # Generación de señal: tiempo acumulado (seg)
        self.t = 0.0

        # Estado del FIR simulado
        self.fir_coeffs = []   # list of int16 Q1.15
        self.fir_loaded = False

        # Máquina de estados RX
        self._rx_state    = 'WAIT_MARKER'
        self._rx_cmd      = 0
        self._rx_len_hi   = 0
        self._rx_expected = 0
        self._rx_payload  = bytearray()

    # ── I/O ─────────────────────────────────────────────────────────────────

    def _write(self, data: bytes):
        try:
            os.write(self.master_fd, data)
        except OSError as e:
            print(f"[SIM] Write error: {e}", flush=True)

    # ── Generación de paquetes ───────────────────────────────────────────────

    def _make_data_pkt(self) -> bytes:
        """Genera un paquete 0xAA con 3 señales sintéticas plausibles."""
        f_signal = 10.0    # Hz — señal geofónica principal
        f_noise  = 60.0    # Hz — interferencia de red (para filtrar)

        # Señal cruda (SAR): señal de 10 Hz + ruido de 60 Hz
        raw_v    = 0.08 * math.sin(2 * math.pi * f_signal * self.t) + \
                   0.03 * math.sin(2 * math.pi * f_noise  * self.t)

        # Post-filtro analógico: 10 Hz puro con atenuación del ruido
        analog_v = 0.075 * math.sin(2 * math.pi * f_signal * self.t)

        # Post-filtro digital: señal aún más limpia (notch elimina 60 Hz)
        digital_v = 0.074 * math.sin(2 * math.pi * f_signal * self.t)

        # Conversión a enteros PSoC
        SAR_FS  = 2048      # 12-bit signed, ±2048 counts = ±1.024 V
        SAR_VREF = 1.024
        ADC_FS  = 65536     # 17-bit signed (usamos 16-bit para centrado)
        VFS     = 6.144     # Rango efectivo CFG4 con BufGain=8: 6.144/8=0.768V pero usamos full

        raw_i    = int(raw_v    / SAR_VREF * SAR_FS)
        raw_i    = max(-SAR_FS, min(SAR_FS - 1, raw_i))

        analog_i = int(analog_v  / VFS * (ADC_FS // 2))
        analog_i = max(-(ADC_FS // 2), min(ADC_FS // 2 - 1, analog_i))

        digital_i = int(digital_v / VFS * (ADC_FS // 2))
        digital_i = max(-(ADC_FS // 2), min(ADC_FS // 2 - 1, digital_i))

        flags     = (self.vdac_mode & 0x01) | ((self.filter_sel & 0x01) << 1)
        # gain_byte: high nibble = log2(buf_gain), low nibble = cfg
        gc        = {1: 0, 2: 1, 4: 2, 8: 3}.get(self.buf_gain, 3)
        gain_byte = (gc << 4) | (self.adc_cfg & 0x0F)

        raw_bytes     = struct.pack('<h', raw_i)
        analog_bytes  = bytes([
            analog_i  & 0xFF,
            (analog_i  >> 8) & 0xFF,
            (analog_i  >> 16) & 0xFF,
        ])
        digital_bytes = bytes([
            digital_i & 0xFF,
            (digital_i >> 8) & 0xFF,
            (digital_i >> 16) & 0xFF,
        ])

        body = bytes([PKT_DATA_HEADER, flags]) + raw_bytes + analog_bytes + digital_bytes + bytes([gain_byte])
        crc  = _crc_xor(body)
        return body + bytes([crc])

    def _make_state_pkt(self) -> bytes:
        """Genera paquete de estado 0xCC (10 bytes)."""
        body = bytes([
            STATE_HEADER, 7,
            self.adc_cfg,
            self.buf_gain,
            1 if self.streaming else 0,
            self.debug_en,
            self.debug_ch,
            self.vdac_mode,
            self.filter_sel,
        ])
        crc = _crc_xor(body)
        return body + bytes([crc])

    def _make_debug_pkt(self, event: int, payload: bytes = b'') -> bytes:
        """Genera paquete de debug 0xDD."""
        hdr = bytes([DBG_HEADER, event, len(payload)])
        crc = _crc_xor(hdr + payload)
        return hdr + payload + bytes([crc])

    # ── Procesamiento de comandos ────────────────────────────────────────────

    def _process_command(self, cmd: int, payload: bytes):
        if cmd == CMD_START:
            self.streaming = True
            print(f"[SIM] CMD_START → streaming ON", flush=True)
            # Emitir evento debug de stream on (0x20)
            self._write(self._make_debug_pkt(0x20))
        elif cmd == CMD_STOP:
            self.streaming = False
            print(f"[SIM] CMD_STOP → streaming OFF", flush=True)
            self._write(self._make_debug_pkt(0x21))
        elif cmd == CMD_SET_MUX_IN and len(payload) >= 1:
            self.vdac_mode = payload[0]
            print(f"[SIM] CMD_SET_MUX_IN → vdac_mode={self.vdac_mode}", flush=True)
        elif cmd == CMD_SET_MUX_OUT and len(payload) >= 1:
            self.filter_sel = payload[0]
            print(f"[SIM] CMD_SET_MUX_OUT → filter_sel={self.filter_sel}", flush=True)
        elif cmd == CMD_SET_ADC and len(payload) >= 2:
            self.adc_cfg  = payload[0]
            self.buf_gain = payload[1]
            print(f"[SIM] CMD_SET_ADC → cfg={self.adc_cfg} gain={self.buf_gain}", flush=True)
        elif cmd == CMD_SET_DEBUG and len(payload) >= 1:
            self.debug_en = payload[0]
        elif cmd == CMD_SET_DBG_CH and len(payload) >= 1:
            self.debug_ch = payload[0]
        elif cmd == CMD_VDAC_LOAD:
            print(f"[SIM] CMD_VDAC_LOAD → {len(payload)} bytes", flush=True)
        elif cmd == CMD_GET_STATE:
            pass
        elif cmd == CMD_LOAD_FIR:
            import struct as _struct
            if len(payload) < 2 or (len(payload) & 1) != 0:
                # Payload inválido (vacío o impar) → ignorar, como el firmware
                print(f"[SIM] CMD_LOAD_FIR → payload inválido ({len(payload)} B), ignorado", flush=True)
                self._write(self._make_state_pkt())
                return
            n_taps = len(payload) // 2
            self.fir_coeffs = [_struct.unpack_from('<h', payload, i*2)[0]
                               for i in range(n_taps)]
            self.fir_loaded = True
            print(f"[SIM] CMD_LOAD_FIR → {n_taps} taps cargados", flush=True)
            # Emitir evento DBG 0x51 (FIR_LOADED) con n_taps
            self._write(self._make_debug_pkt(0x51, bytes([n_taps])))
        elif cmd == CMD_FIR_CLEAR:
            self.fir_coeffs = []
            self.fir_loaded = False
            print(f"[SIM] CMD_FIR_CLEAR → FIR desactivado", flush=True)
            # Emitir evento DBG 0x52 (FIR_CLEAR)
            self._write(self._make_debug_pkt(0x52))

        # Confirmar siempre con paquete de estado
        self._write(self._make_state_pkt())

    # ── Parser RX (misma máquina de estados que el firmware) ────────────────

    def _parse_rx_byte(self, b: int):
        if self._rx_state == 'WAIT_MARKER':
            if b == CMD_MARKER:
                self._rx_state = 'WAIT_CMD'

        elif self._rx_state == 'WAIT_CMD':
            self._rx_cmd   = b
            self._rx_state = 'WAIT_LEN'

        elif self._rx_state == 'WAIT_LEN':
            if self._rx_cmd in (CMD_VDAC_LOAD, CMD_LOAD_FIR):
                self._rx_len_hi = b
                self._rx_state  = 'WAIT_LEN2'
            elif b == 0:
                self._process_command(self._rx_cmd, b'')
                self._rx_state = 'WAIT_MARKER'
            else:
                self._rx_expected = b
                self._rx_payload  = bytearray()
                self._rx_state    = 'WAIT_PAYLOAD'

        elif self._rx_state == 'WAIT_LEN2':
            self._rx_expected = (self._rx_len_hi << 8) | b
            self._rx_payload  = bytearray()
            if self._rx_expected == 0:
                self._rx_state = 'WAIT_MARKER'
            else:
                self._rx_state = 'WAIT_PAYLOAD'

        elif self._rx_state == 'WAIT_PAYLOAD':
            self._rx_payload.append(b)
            if len(self._rx_payload) >= self._rx_expected:
                self._process_command(self._rx_cmd, bytes(self._rx_payload))
                self._rx_state = 'WAIT_MARKER'

    # ── Hilo RX ──────────────────────────────────────────────────────────────

    def rx_thread(self):
        """Lee comandos del puerto virtual de forma no bloqueante."""
        while True:
            try:
                data = os.read(self.master_fd, 256)
                for b in data:
                    self._parse_rx_byte(b)
            except OSError:
                break

    # ── Loop TX ──────────────────────────────────────────────────────────────

    def tx_loop(self):
        """Envía paquetes de datos a 1500 Hz cuando streaming está activo.

        Estrategia de batching: acumula los paquetes que habrían salido en el
        último intervalo y los envía juntos para no perder muestras por el
        scheduling del OS (que no puede dormir con precisión de 666 μs).
        """
        interval = 1.0 / SAMPLE_RATE
        last_tx  = time.monotonic()

        # Emitir evento de boot al arrancar
        self._write(self._make_debug_pkt(0x12))  # BOOT_USB

        while True:
            time.sleep(0.005)   # 5 ms por tick → batches de ~8 paquetes a 1500 Hz

            now     = time.monotonic()
            elapsed = now - last_tx
            last_tx = now

            if self.streaming:
                n_pkts = int(elapsed * SAMPLE_RATE)
                batch  = bytearray()
                for _ in range(n_pkts):
                    batch.extend(self._make_data_pkt())
                    self.t += interval
                if batch:
                    self._write(bytes(batch))
            else:
                self.t += elapsed


def main():
    # Crear par de PTY (master para el simulador, slave para el osciloscopio)
    master_fd, slave_fd = os.openpty()

    # CRÍTICO: poner el slave en modo raw ANTES de cerrarlo.
    # Sin esto el PTY en modo cooked convierte LF→CRLF y filtra bytes especiales
    # (0x03, 0x04, 0x11, etc.), corrompiendo los paquetes binarios.
    tty.setraw(slave_fd)

    slave_path = os.ttyname(slave_fd)

    # También poner master en modo raw para evitar eco y conversiones
    tty.setraw(master_fd)

    # Cerrar el slave en este proceso: el osciloscopio lo abrirá
    os.close(slave_fd)

    print(f"[SIM] Puerto virtual listo: {slave_path}", flush=True)
    print(f"[SIM] Conecta el osciloscopio a: {slave_path}", flush=True)
    print(f"[SIM] Velocidad: {SAMPLE_RATE} Hz, protocolo 0xAA/0xCC/0xDD", flush=True)

    sim = PSoCSimulator(master_fd)

    # Hilo RX en background
    t_rx = threading.Thread(target=sim.rx_thread, daemon=True)
    t_rx.start()

    # Loop TX en main thread
    try:
        sim.tx_loop()
    except KeyboardInterrupt:
        print("\n[SIM] Simulador detenido.", flush=True)


if __name__ == '__main__':
    main()
