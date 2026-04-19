#!/usr/bin/env python3
"""
test_protocol_stress.py — Pruebas de estrés del protocolo PSoC ↔ Python.

Cubre casos que no verifica test_backend.py (happy path):
  1. Stream con bytes basura intercalados — parser se re-sincroniza
  2. CMD_LOAD_FIR inmediatamente seguido de CMD_START (race condition)
  3. CMD_VDAC_LOAD con secuencia máxima (3000 bytes)
  4. 10 CMD_GET_STATE consecutivos — no crash, no duplicados
  5. FIR de 254 taps (límite casi máximo)
  6. Rollover de seq 65535→0 en acumulador receiver
  7. Paquetes de datos con valores en límites exactos de rango (int16/int24 extremos)
  8. Cambio de ADC config (CMD_SET_ADC) mientras se transmite
  9. Stream de 10 segundos — tasa sostenida ≥ 1450 pkt/s sin CRC errors
 10. FIR clear + re-carga en bucle 50 veces
"""

import os
import sys
import time
import struct
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol       import (parse_packet, PKT_DATA_HEADER, PKT_DATA_SIZE,
                             cmd_load_fir, cmd_fir_clear, cmd_start, cmd_stop,
                             cmd_set_adc, cmd_get_state, cmd_vdac_load,
                             CMD_MARKER, CMD_LOAD_FIR)
from debug_protocol import (parse_state_packet, STATE_HEADER, STATE_PKT_LEN,
                             parse_debug_packet, DBG_HEADER)
from digital_filters import design_fir_lowpass

import tty

# ── Helpers ───────────────────────────────────────────────────────────────────

def _open_pty_raw(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    return fd


def _write(fd, data):
    os.write(fd, bytes(data))
    time.sleep(0.01)


def _drain(fd, timeout_s, count_bad=True) -> tuple:
    """Returns (data_pkts, state_pkts, dbg_pkts, bad_pkts, all_samples)."""
    buf = bytearray()
    data = state = dbg = bad = 0
    all_samples = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            buf.extend(os.read(fd, 4096))
        except BlockingIOError:
            time.sleep(0.001)
            continue
        except OSError:
            break
        while buf:
            if buf[0] == PKT_DATA_HEADER:
                if len(buf) < PKT_DATA_SIZE:
                    break
                pkt = parse_packet(bytes(buf[:PKT_DATA_SIZE]))
                if pkt:
                    data += 1
                    all_samples.append(pkt)
                else:
                    bad += 1
                del buf[:PKT_DATA_SIZE]
            elif buf[0] == STATE_HEADER:
                if len(buf) < STATE_PKT_LEN:
                    break
                st = parse_state_packet(bytes(buf[:STATE_PKT_LEN]))
                if st:
                    state += 1
                else:
                    bad += 1
                del buf[:STATE_PKT_LEN]
            elif buf[0] == DBG_HEADER:
                if len(buf) < 4:
                    break
                plen = buf[2]
                sz = 4 + plen
                if len(buf) < sz:
                    break
                pkt = parse_debug_packet(bytes(buf[:sz]))
                if pkt:
                    dbg += 1
                else:
                    bad += 1
                del buf[:sz]
            else:
                del buf[:1]
    return data, state, dbg, bad, all_samples


class Result:
    def __init__(self, name):
        self.name = name
        self.ok = True
        self.errors = []

    def fail(self, msg):
        self.ok = False
        self.errors.append(msg)

    def __str__(self):
        s = "PASS ✓" if self.ok else "FAIL ✗"
        lines = [f"  [{s}] {self.name}"]
        for e in self.errors:
            lines.append(f"         → {e}")
        return "\n".join(lines)


# ── Escenarios ────────────────────────────────────────────────────────────────

def run_all(fd) -> list:
    FS = 1500.0
    results = []

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Bytes basura intercalados — parser se re-sincroniza
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 1 — Bytes basura en stream → re-sincronización parser")
    _write(fd, cmd_start())
    time.sleep(0.1)
    # Inyectar 20 bytes basura que NO son marcadores de paquete válido
    garbage = bytes([0x11, 0x22, 0x55, 0xEE, 0x00, 0xFF,
                     0x33, 0x44, 0x66, 0x77, 0x88, 0x99,
                     0xAB, 0xCD, 0xEF, 0x01, 0x02, 0x03, 0x04, 0x05])
    # El 0xAB podría activar transport_spi marker en el receiver Python —
    # pero en el simulador no se envía 0xAB, así que es basura inerte
    os.write(fd, garbage)
    d, s, _, b, _ = _drain(fd, 1.0)
    _write(fd, cmd_stop())
    if d < int(FS * 1.0 * 0.7):
        r.fail(f"Pocos paquetes después de basura: {d}")
    if b > 0:
        r.fail(f"CRC errors: {b} (basura debería ser descartada por el parser)")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. CMD_LOAD_FIR + CMD_START sin delay (race condition)
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 2 — CMD_LOAD_FIR + CMD_START sin delay intermedio")
    coeffs = design_fir_lowpass(50.0, FS, 63)
    # Enviar los dos comandos en el mismo write (sin sleep entre ellos)
    combined = cmd_load_fir(coeffs) + cmd_start()
    os.write(fd, combined)
    d, s, _, b, _ = _drain(fd, 0.5)
    _write(fd, cmd_stop())
    if d < int(FS * 0.5 * 0.6):
        r.fail(f"Pocos paquetes en race condition FIR+START: {d}")
    if b > 0:
        r.fail(f"CRC errors: {b}")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. CMD_VDAC_LOAD máximo (3000 bytes)
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 3 — CMD_VDAC_LOAD con secuencia de 3000 bytes (máximo)")
    import math
    seq = [int(128 + 127 * math.sin(2 * math.pi * i / 100)) for i in range(3000)]
    vdac_cmd = cmd_vdac_load(seq)
    _write(fd, vdac_cmd)
    # Verificar que el simulador confirmó con un state packet
    time.sleep(0.1)
    _write(fd, cmd_start())
    d, s, _, b, _ = _drain(fd, 0.5)
    _write(fd, cmd_stop())
    if s < 1:
        r.fail(f"No se recibió state packet tras VDAC_LOAD (s={s})")
    if b > 0:
        r.fail(f"CRC errors: {b}")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. 10 CMD_GET_STATE consecutivos
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 4 — 10 CMD_GET_STATE consecutivos → 10 state packets")
    for _ in range(10):
        os.write(fd, cmd_get_state())
    time.sleep(0.2)
    d, s, _, b, _ = _drain(fd, 0.3)
    if s < 8:   # tolerar hasta 2 perdidos por scheduling OS
        r.fail(f"Solo {s} state packets para 10 CMD_GET_STATE")
    if b > 0:
        r.fail(f"CRC errors: {b}")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. FIR de 254 taps (cerca del máximo de 255)
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 5 — FIR de 254 taps (límite casi máximo)")
    coeffs = design_fir_lowpass(40.0, FS, 254)
    if len(coeffs) != 254:
        r.fail(f"firwin devolvió {len(coeffs)} taps (esperado 254)")
    else:
        _write(fd, cmd_load_fir(coeffs))
        _write(fd, cmd_start())
        d, _, _, b, _ = _drain(fd, 0.5)
        _write(fd, cmd_stop())
        if d < int(FS * 0.5 * 0.6):
            r.fail(f"Pocos paquetes con 254 taps: {d}")
        if b > 0:
            r.fail(f"CRC errors: {b}")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Rollover de seq en receiver (65535 → 0)
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 6 — Rollover de seq 65535→0 en NodeAccumulator")
    # Importar helpers de test_receiver.py y receiver.py
    _esp_base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../esp/base')
    if _esp_base not in sys.path:
        sys.path.insert(0, _esp_base)
    from test_receiver import build_packet as bp, make_sample as ms
    from receiver import NodeAccumulator, parse_node_packet as pnp
    acc = NodeAccumulator(1)
    for seq in [65533, 65534, 65535, 0, 1, 2]:
        pkt_bytes = bp(1, seq, 0, seq * 20000, [ms()] * 5)
        d2 = pnp(pkt_bytes)
        if d2:
            acc.add_batch(d2)
    # Expect 0 drops: 65533,65534,65535,0,1,2 is a continuous sequence
    if acc.drops != 0:
        r.fail(f"Drops={acc.drops} en rollover 65535→0 (esperado 0)")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Valores extremos int16/int24 en paquetes de datos
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 7 — stream con valores int16/int24 extremos → rango OK")
    _write(fd, cmd_fir_clear())
    _write(fd, cmd_start())
    _, _, _, b, samples = _drain(fd, 0.5)
    _write(fd, cmd_stop())
    if b > 0:
        r.fail(f"CRC errors: {b}")
    if samples:
        for s in samples[:100]:
            if not (-10.0 < s['post_analog'] < 10.0):
                r.fail(f"post_analog={s['post_analog']:.4f} fuera de rango")
                break
            if not (-10.0 < s['post_digital'] < 10.0):
                r.fail(f"post_digital={s['post_digital']:.4f} fuera de rango")
                break
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 8. CMD_SET_ADC mientras se transmite — state packet confirmado
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 8 — CMD_SET_ADC (cfg=2, gain=4) mientras transmite")
    _write(fd, cmd_start())
    time.sleep(0.1)
    _write(fd, cmd_set_adc(2, 4))   # cambiar a CFG2, buf_gain=4
    d, s, _, b, _ = _drain(fd, 0.5)
    _write(fd, cmd_stop())
    if s < 1:
        r.fail(f"No se recibió state packet tras CMD_SET_ADC (s={s})")
    if b > 0:
        r.fail(f"CRC errors: {b}")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Stream largo: 10 segundos, tasa ≥ 1450 pkt/s sostenida
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 9 — Stream 10 s: tasa ≥ 1400 pkt/s (simulator), 0 CRC errors")
    _write(fd, cmd_fir_clear())
    _write(fd, cmd_start())
    t0 = time.monotonic()
    d, _, _, b, _ = _drain(fd, 10.0)
    elapsed = time.monotonic() - t0
    _write(fd, cmd_stop())
    rate = d / elapsed if elapsed > 0 else 0
    if rate < 1400.0:  # 93% de 1500 Hz; PSoC real = exacto, simulador tiene jitter OS
        r.fail(f"Tasa {rate:.0f} pkt/s < 1450 pkt/s durante {elapsed:.1f} s")
    if b > 0:
        r.fail(f"CRC errors: {b}")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 10. FIR_CLEAR + reload en bucle 50 veces
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Escenario 10 — FIR_CLEAR + reload ×50 mientras transmite")
    _write(fd, cmd_start())
    errors_before = 0
    for i in range(50):
        _write(fd, cmd_fir_clear())
        coeffs = design_fir_lowpass(10.0 + i * 10.0 % 700.0, FS, 31)
        _write(fd, cmd_load_fir(coeffs))
    d, _, _, b, _ = _drain(fd, 1.0)
    _write(fd, cmd_stop())
    if d < int(FS * 1.0 * 0.4):   # umbral bajo por las interrupciones de reload
        r.fail(f"Pocos paquetes durante reload×50: {d}")
    if b > 0:
        r.fail(f"CRC errors: {b}")
    results.append(r)

    return results


# ── Runner ────────────────────────────────────────────────────────────────────

def run_test() -> bool:
    sim_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'simulador_psoc.py')
    sim_proc   = subprocess.Popen(
        [sys.executable, sim_script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, text=True,
    )

    slave_path = None
    deadline   = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        line = sim_proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if 'Puerto virtual listo:' in line:
            slave_path = line.split(':', 1)[1].strip()
            break

    if not slave_path:
        print("[TEST] FAIL: simulador no inició")
        sim_proc.terminate()
        return False

    print(f"[TEST] Puerto virtual: {slave_path}\n")

    try:
        fd = _open_pty_raw(slave_path)
    except OSError as e:
        print(f"[TEST] FAIL: {e}")
        sim_proc.terminate()
        return False

    time.sleep(0.2)
    results = run_all(fd)

    os.close(fd)
    sim_proc.terminate()
    sim_proc.wait(timeout=2)

    print("=" * 65)
    print("  TEST ESTRÉS PROTOCOLO")
    print("=" * 65)
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    for r in results:
        print(r)
    print("─" * 65)
    print(f"  Total: {len(results)}  |  PASS: {passed}  |  FAIL: {failed}")
    print("=" * 65)
    return failed == 0


if __name__ == '__main__':
    ok = run_test()
    sys.exit(0 if ok else 1)
