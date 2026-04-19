#!/usr/bin/env python3
"""
test_vdac_state.py — Tests para vdac_generator.py y parse_state_packet.

Cubre:
  1. Rango de salida [0,255] para todos los generadores
  2. Valor DC de silencio = 128
  3. Impulso: un solo sample != 128, resto = 128
  4. from_array con all-zeros (sin ZeroDivision)
  5. _to_vdac con amplitud > 1.0 (debe clampear, no explotar)
  6. Sine / chirp / ricker / gauss3 tienen la longitud correcta
  7. from_array normaliza correctamente señales grandes y pequeñas
  8. cmd_vdac_load con 256 muestras → len_hi = 1
  9. cmd_vdac_load con 0 muestras → length = 0
 10. parse_state_packet — happy path
 11. parse_state_packet — CRC corrompido → None
 12. parse_state_packet — marcador incorrecto → None
 13. parse_state_packet — payload demasiado corto → None
 14. parse_state_packet — todos los campos se extraen correctamente
 15. parse_state_packet — fixed byte != 7 → None
"""

import sys
import os
import math
import struct
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vdac_generator import (ricker, gauss3, sine, chirp, impulse, silence,
                             from_array, _to_vdac)
from protocol import cmd_vdac_load
from debug_protocol import parse_state_packet, STATE_HEADER, STATE_PKT_LEN


class Result:
    def __init__(self, name): self.name = name; self.ok = True; self.errors = []
    def fail(self, m): self.ok = False; self.errors.append(m)
    def __str__(self):
        s = "PASS ✓" if self.ok else "FAIL ✗"
        lines = [f"  [{s}] {self.name}"]
        for e in self.errors: lines.append(f"         → {e}")
        return "\n".join(lines)


def _make_state_pkt(adc_cfg=4, buf_gain=8, streaming=1, debug_en=0,
                    debug_ch=0, vdac_mode=0, filter_sel=0,
                    corrupt_crc=False, bad_marker=False, bad_fixed=False):
    """Build a valid 10-byte 0xCC state packet."""
    fixed_byte = 9 if bad_fixed else 7
    raw = bytes([
        STATE_HEADER if not bad_marker else 0xBB,
        fixed_byte,
        adc_cfg & 0xFF,
        buf_gain & 0xFF,
        streaming & 0xFF,
        debug_en & 0xFF,
        debug_ch & 0xFF,
        vdac_mode & 0xFF,
        filter_sel & 0xFF,
    ])
    crc = 0
    for b in raw:
        crc ^= b
    if corrupt_crc:
        crc ^= 0xFF
    return raw + bytes([crc])


def run():
    results = []

    # ── 1. Todos los generadores: salida en [0, 255] ──────────────────────────
    r = Result("vdac_generator — rango de salida [0,255] para todos los generadores")
    for name, seq in [
        ("silence", silence()),
        ("impulse", impulse()),
        ("sine 10Hz", sine(10.0, duration_s=0.1)),
        ("chirp 10-100Hz", chirp(10.0, 100.0, duration_s=0.1)),
        ("ricker", ricker(sigma_s=0.1)),
        ("gauss3", gauss3(sigma_s=0.1)),
    ]:
        if any(v < 0 or v > 255 for v in seq):
            r.fail(f"{name}: valor fuera de [0,255] → min={min(seq)} max={max(seq)}")
        if not all(isinstance(v, int) for v in seq):
            r.fail(f"{name}: contiene no-enteros")
    results.append(r)

    # ── 2. Silencio = 128 exacto ──────────────────────────────────────────────
    r = Result("silence() → todos los valores = 128 (DC midpoint)")
    s = silence(fs=100.0)
    if len(s) != 100:
        r.fail(f"len={len(s)} esperado 100")
    if any(v != 128 for v in s):
        r.fail(f"Hay valores != 128: {set(s)}")
    results.append(r)

    # ── 3. Impulso: un solo sample desviado, resto 128 ────────────────────────
    r = Result("impulse() → un sample central != 128, todos los demás = 128")
    imp = impulse(fs=100.0, amplitude=0.9)
    if len(imp) != 100:
        r.fail(f"len={len(imp)} esperado 100")
    non_dc = [i for i, v in enumerate(imp) if v != 128]
    if len(non_dc) != 1:
        r.fail(f"Esperaba 1 sample != 128, hay {len(non_dc)}: indices {non_dc[:5]}")
    elif non_dc[0] != 50:   # center = n//2 = 50 for fs=100
        r.fail(f"Impulso en índice {non_dc[0]}, esperado 50")
    results.append(r)

    # ── 4. from_array con all-zeros — sin ZeroDivision ───────────────────────
    r = Result("from_array(all-zeros) → todos los valores = 128 (sin ZeroDivision)")
    try:
        out = from_array(np.zeros(50))
        if any(v != 128 for v in out):
            r.fail(f"Valores != 128 con entrada cero: {set(out)}")
    except ZeroDivisionError:
        r.fail("ZeroDivisionError con entrada all-zeros")
    results.append(r)

    # ── 5. _to_vdac con amplitude > 1.0 — clampea sin explotar ──────────────
    r = Result("_to_vdac(amplitude=2.0) → salida sigue en [0,255] (clampea a ±1)")
    sig = np.sin(np.linspace(0, 2*math.pi, 100))
    try:
        out = _to_vdac(sig, amplitude=2.0)
        if any(v < 0 or v > 255 for v in out):
            r.fail(f"Valores fuera de rango: min={min(out)} max={max(out)}")
    except Exception as e:
        r.fail(f"Excepción: {e}")
    results.append(r)

    # ── 6. Longitudes correctas ───────────────────────────────────────────────
    r = Result("Generadores de 1s @ fs=500 → longitud = 500 samples")
    for name, seq in [
        ("sine", sine(10.0, duration_s=1.0, fs=500.0)),
        ("chirp", chirp(10.0, 100.0, duration_s=1.0, fs=500.0)),
        ("ricker", ricker(sigma_s=0.05, fs=500.0)),
        ("gauss3", gauss3(sigma_s=0.05, fs=500.0)),
        ("impulse", impulse(fs=500.0)),
    ]:
        if len(seq) != 500:
            r.fail(f"{name}: len={len(seq)} esperado 500")
    results.append(r)

    # ── 7. from_array normaliza — señal amplitud 1000 → misma forma que amp 1 ─
    r = Result("from_array — normaliza independiente de la amplitud de entrada")
    t = np.linspace(0, 1, 100)
    sig_unit = np.sin(2*math.pi*5*t)
    sig_big  = sig_unit * 1000.0
    out_unit = from_array(sig_unit)
    out_big  = from_array(sig_big)
    if out_unit != out_big:
        diffs = sum(1 for a, b in zip(out_unit, out_big) if a != b)
        r.fail(f"Normalización diferente: {diffs} samples difieren")
    results.append(r)

    # ── 8. cmd_vdac_load con 256 muestras → len_hi = 1 ───────────────────────
    r = Result("cmd_vdac_load(256 samples) → len_hi=1, len_lo=0")
    b = cmd_vdac_load(list(range(256)))
    if b[0] != 0xBB or b[1] != 0x06:
        r.fail(f"Marcador/cmd: {b[:2].hex()}")
    n = (b[2] << 8) | b[3]
    if n != 256:
        r.fail(f"n={n} esperado 256")
    if b[2] != 1 or b[3] != 0:
        r.fail(f"len_hi={b[2]} len_lo={b[3]} — esperado 1, 0")
    if len(b) != 4 + 256:
        r.fail(f"Total len={len(b)} esperado 260")
    results.append(r)

    # ── 9. cmd_vdac_load([]) → length = 0, solo header ───────────────────────
    r = Result("cmd_vdac_load([]) → len=0, frame = [BB 06 00 00]")
    b = cmd_vdac_load([])
    if b != bytes([0xBB, 0x06, 0x00, 0x00]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── 10. parse_state_packet — happy path ───────────────────────────────────
    r = Result("parse_state_packet — happy path, todos los campos correctos")
    raw = _make_state_pkt(adc_cfg=4, buf_gain=8, streaming=1, debug_en=1,
                          debug_ch=2, vdac_mode=1, filter_sel=3)
    st = parse_state_packet(raw)
    if st is None:
        r.fail("Devolvió None en happy path")
    else:
        if st['adc_cfg']  != 4: r.fail(f"adc_cfg={st['adc_cfg']}")
        if st['buf_gain'] != 8: r.fail(f"buf_gain={st['buf_gain']}")
        if not st['streaming']:  r.fail("streaming debería ser True")
        if not st['debug_en']:   r.fail("debug_en debería ser True")
        if st['debug_ch']   != 2: r.fail(f"debug_ch={st['debug_ch']}")
        if st['vdac_mode']  != 1: r.fail(f"vdac_mode={st['vdac_mode']}")
        if st['filter_sel'] != 3: r.fail(f"filter_sel={st['filter_sel']}")
    results.append(r)

    # ── 11. parse_state_packet — CRC corrompido ───────────────────────────────
    r = Result("parse_state_packet — CRC corrompido → None")
    raw = _make_state_pkt(corrupt_crc=True)
    if parse_state_packet(raw) is not None:
        r.fail("No rechazó CRC incorrecto")
    results.append(r)

    # ── 12. parse_state_packet — marcador incorrecto ──────────────────────────
    r = Result("parse_state_packet — marcador != 0xCC → None")
    raw = _make_state_pkt(bad_marker=True)
    if parse_state_packet(raw) is not None:
        r.fail("No rechazó marcador incorrecto")
    results.append(r)

    # ── 13. parse_state_packet — paquete demasiado corto → None ──────────────
    r = Result("parse_state_packet — datos < 10 bytes → None")
    if parse_state_packet(bytes([0xCC] * 5)) is not None:
        r.fail("No rechazó paquete corto")
    results.append(r)

    # ── 14. parse_state_packet — streaming=0 es falsy ────────────────────────
    r = Result("parse_state_packet — streaming=0 → bool False")
    raw = _make_state_pkt(streaming=0, debug_en=0)
    st = parse_state_packet(raw)
    if st is None:
        r.fail("None")
    else:
        if st['streaming']:  r.fail("streaming debería ser False")
        if st['debug_en']:   r.fail("debug_en debería ser False")
    results.append(r)

    # ── 15. parse_state_packet — fixed byte != 7 → None ─────────────────────
    r = Result("parse_state_packet — byte[1] != 7 → None (protocolo incorrecto)")
    raw = _make_state_pkt(bad_fixed=True)
    if parse_state_packet(raw) is not None:
        r.fail("No rechazó byte fijo incorrecto")
    results.append(r)

    return results


def main():
    print("=" * 62)
    print("  TEST VDAC GENERATOR + PARSE STATE PACKET")
    print("=" * 62)
    results = run()
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    for r in results: print(r)
    print("─" * 62)
    print(f"  Total: {len(results)}  |  PASS: {passed}  |  FAIL: {failed}")
    print("=" * 62)
    return failed == 0


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
