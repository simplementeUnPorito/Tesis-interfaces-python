#!/usr/bin/env python3
"""
test_backend.py — Prueba headless del backend de parseo (SIN GUI).

Lanza simulador_psoc.py como subproceso, conecta via puerto PTY virtual,
envía CMD_START y verifica que los paquetes 0xAA y 0xCC se reciben y
decodifican correctamente durante 5 segundos.

No usa Tkinter, PyQt ni matplotlib — apto para entorno headless/sin Display.

Uso:
    ./venv/bin/python test_backend.py
"""

import os
import sys
import time
import subprocess
import termios
import tty

# Asegurar que los imports de protocolo funcionen
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol       import parse_packet, PKT_DATA_HEADER, PKT_DATA_SIZE, \
                           cmd_load_fir, cmd_fir_clear, cmd_start
from debug_protocol import parse_state_packet, STATE_HEADER, STATE_PKT_LEN, \
                           parse_debug_packet, DBG_HEADER
from digital_filters import design_fir_lowpass, coeffs_to_q15

TEST_DURATION = 5.0     # segundos de recepción
MIN_PACKET_RATE = 0.75  # al menos 75% de los paquetes esperados deben llegar


def run_test() -> bool:
    # ── 1. Iniciar simulador como subproceso ─────────────────────────────────
    sim_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'simulador_psoc.py')
    sim_proc = subprocess.Popen(
        [sys.executable, sim_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
    )

    # ── 2. Leer path del puerto virtual ─────────────────────────────────────
    slave_path = None
    deadline   = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        line = sim_proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        print(f"  {line}", flush=True)
        if 'Puerto virtual listo:' in line:
            slave_path = line.split(':', 1)[1].strip()
            break

    if not slave_path:
        print("[TEST] FAIL: El simulador no reportó un puerto virtual.")
        sim_proc.terminate()
        return False

    print(f"\n[TEST] Puerto virtual: {slave_path}")

    # ── 3. Abrir puerto serial raw (sin pyserial para evitar dependencias extra) ──
    try:
        fd = os.open(slave_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        # Modo raw: sin eco, sin conversiones de newline, sin señales especiales
        tty.setraw(fd)
    except OSError as e:
        print(f"[TEST] FAIL: No se pudo abrir {slave_path}: {e}")
        sim_proc.terminate()
        return False

    time.sleep(0.2)  # dar tiempo al simulador

    # ── 4a. Enviar CMD_LOAD_FIR (filtro pasa-bajos 20 Hz, 63 taps) ──────────
    try:
        fs      = 1500.0
        coeffs  = design_fir_lowpass(20.0, fs, n_taps=63)
        fir_cmd = cmd_load_fir(coeffs)
        os.write(fd, fir_cmd)
        print(f"[TEST] CMD_LOAD_FIR enviado ({len(coeffs_to_q15(coeffs))} taps, "
              f"{len(fir_cmd)} bytes total)", flush=True)
        time.sleep(0.05)
    except OSError as e:
        print(f"[TEST] FAIL: No se pudo escribir CMD_LOAD_FIR: {e}")
        os.close(fd)
        sim_proc.terminate()
        return False

    # ── 4b. Enviar CMD_START (BB 01 00) ─────────────────────────────────────
    try:
        os.write(fd, bytes([0xBB, 0x01, 0x00]))
        print("[TEST] CMD_START enviado")
    except OSError as e:
        print(f"[TEST] FAIL: No se pudo escribir CMD_START: {e}")
        os.close(fd)
        sim_proc.terminate()
        return False

    # ── 5. Recibir y parsear paquetes ────────────────────────────────────────
    buf             = bytearray()
    pkt_data_count  = 0
    pkt_state_count = 0
    pkt_dbg_count   = 0
    pkt_bad_count   = 0
    range_errors    = []

    deadline = time.monotonic() + TEST_DURATION
    last_report = time.monotonic()

    while time.monotonic() < deadline:
        try:
            chunk = os.read(fd, 1024)
            buf.extend(chunk)
        except BlockingIOError:
            time.sleep(0.001)
            continue
        except OSError as e:
            print(f"[TEST] FAIL: Error de lectura: {e}")
            break

        # Parsear todos los paquetes disponibles en el buffer
        while buf:
            if buf[0] == PKT_DATA_HEADER:
                if len(buf) < PKT_DATA_SIZE:
                    break
                pkt = parse_packet(bytes(buf[:PKT_DATA_SIZE]))
                if pkt is not None:
                    pkt_data_count += 1
                    # Validar rangos plausibles
                    if not (-5.0 < pkt['raw_input'] < 5.0):
                        range_errors.append(f"raw_input={pkt['raw_input']:.4f} fuera de [-5,5] V")
                    if not (-10.0 < pkt['post_analog'] < 10.0):
                        range_errors.append(f"post_analog={pkt['post_analog']:.4f} fuera de rango")
                    if not (-10.0 < pkt['post_digital'] < 10.0):
                        range_errors.append(f"post_digital={pkt['post_digital']:.4f} fuera de rango")
                    del buf[:PKT_DATA_SIZE]
                else:
                    pkt_bad_count += 1
                    del buf[:1]   # avanzar de a 1 byte para re-sincronizar

            elif buf[0] == STATE_HEADER:
                if len(buf) < STATE_PKT_LEN:
                    break
                st = parse_state_packet(bytes(buf[:STATE_PKT_LEN]))
                if st is not None:
                    pkt_state_count += 1
                else:
                    pkt_bad_count += 1
                del buf[:STATE_PKT_LEN]

            elif buf[0] == DBG_HEADER:
                if len(buf) < 4:
                    break
                plen     = buf[2]
                pkt_size = 4 + plen
                if len(buf) < pkt_size:
                    break
                dbg = parse_debug_packet(bytes(buf[:pkt_size]))
                if dbg is not None:
                    pkt_dbg_count += 1
                else:
                    pkt_bad_count += 1
                del buf[:pkt_size]

            else:
                # Byte basura — descartar
                del buf[:1]

        # Reporte de progreso cada segundo
        now = time.monotonic()
        if now - last_report >= 1.0:
            remaining = deadline - now
            print(f"[TEST] t={TEST_DURATION - remaining:.1f}s | "
                  f"data={pkt_data_count} state={pkt_state_count} "
                  f"dbg={pkt_dbg_count} bad={pkt_bad_count}", flush=True)
            last_report = now

    os.close(fd)
    sim_proc.terminate()
    sim_proc.wait(timeout=2)

    # ── 6. Evaluar resultados ─────────────────────────────────────────────────
    expected_min = int(TEST_DURATION * 1500 * MIN_PACKET_RATE)

    print(f"\n{'─'*55}")
    print(f"[TEST] Resultados tras {TEST_DURATION:.0f} s de captura:")
    print(f"  Paquetes 0xAA (data):   {pkt_data_count:>6}  (mínimo: {expected_min})")
    print(f"  Paquetes 0xCC (state):  {pkt_state_count:>6}")
    print(f"  Paquetes 0xDD (debug):  {pkt_dbg_count:>6}")
    print(f"  Paquetes malos (CRC):   {pkt_bad_count:>6}")
    print(f"  Errores de rango:       {len(range_errors):>6}")

    ok_rate   = pkt_data_count >= expected_min
    ok_crc    = pkt_bad_count == 0
    ok_ranges = len(range_errors) == 0

    success = ok_rate and ok_crc and ok_ranges

    if not ok_rate:
        print(f"  ✗ Tasa de paquetes insuficiente ({pkt_data_count} < {expected_min})")
    if not ok_crc:
        print(f"  ✗ Paquetes con CRC incorrecto: {pkt_bad_count}")
    if not ok_ranges:
        for e in range_errors[:5]:
            print(f"  ✗ {e}")
        if len(range_errors) > 5:
            print(f"  ... y {len(range_errors)-5} errores más")

    status = "PASS ✓" if success else "FAIL ✗"
    print(f"{'─'*55}")
    print(f"[TEST] {status}")
    print(f"{'─'*55}\n")

    return success


if __name__ == '__main__':
    print("=" * 55)
    print("  TEST BACKEND — Protocolo PSoC (headless)")
    print("=" * 55)
    ok = run_test()
    sys.exit(0 if ok else 1)
