#!/usr/bin/env python3
"""
test_protocol_builders.py — Pruebas unitarias de los command builders en protocol.py.

Verifica que cada función genera exactamente los bytes correctos que el
firmware PSoC espera recibir, incluyendo el marcador 0xBB, el código de
comando, la longitud y el payload.

No requiere hardware ni simulador.
"""

import sys
import os
import struct
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol import (
    cmd_start, cmd_stop, cmd_set_mux_in, cmd_set_mux_out,
    cmd_set_adc, cmd_vdac_load, cmd_set_debug, cmd_set_debug_ch,
    cmd_get_state, cmd_load_fir, cmd_fir_clear,
    CMD_MARKER, CMD_START, CMD_STOP, CMD_SET_MUX_IN, CMD_SET_MUX_OUT,
    CMD_SET_ADC, CMD_VDAC_LOAD, CMD_SET_DEBUG, CMD_SET_DBG_CH,
    CMD_GET_STATE, CMD_LOAD_FIR, CMD_FIR_CLEAR
)


class Result:
    def __init__(self, name): self.name = name; self.ok = True; self.errors = []
    def fail(self, m): self.ok = False; self.errors.append(m)
    def __str__(self):
        s = "PASS ✓" if self.ok else "FAIL ✗"
        lines = [f"  [{s}] {self.name}"]
        for e in self.errors: lines.append(f"         → {e}")
        return "\n".join(lines)


def run():
    results = []

    # ── cmd_start ─────────────────────────────────────────────────────────────
    r = Result("cmd_start → [BB 01 00]")
    b = cmd_start()
    if b != bytes([0xBB, 0x01, 0x00]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── cmd_stop ──────────────────────────────────────────────────────────────
    r = Result("cmd_stop → [BB 02 00]")
    b = cmd_stop()
    if b != bytes([0xBB, 0x02, 0x00]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── cmd_set_mux_in ────────────────────────────────────────────────────────
    r = Result("cmd_set_mux_in(1) → [BB 03 01 01]")
    b = cmd_set_mux_in(1)
    if b != bytes([0xBB, 0x03, 0x01, 0x01]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── cmd_set_mux_out ───────────────────────────────────────────────────────
    r = Result("cmd_set_mux_out(0) → [BB 04 01 00]")
    b = cmd_set_mux_out(0)
    if b != bytes([0xBB, 0x04, 0x01, 0x00]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── cmd_set_adc ───────────────────────────────────────────────────────────
    r = Result("cmd_set_adc(cfg=4, bg=8) → [BB 05 02 04 08]")
    b = cmd_set_adc(4, 8)
    if b != bytes([0xBB, 0x05, 0x02, 0x04, 0x08]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    r = Result("cmd_set_adc(cfg=1, bg=1) → [BB 05 02 01 01]")
    b = cmd_set_adc(1, 1)
    if b != bytes([0xBB, 0x05, 0x02, 0x01, 0x01]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── cmd_vdac_load ─────────────────────────────────────────────────────────
    r = Result("cmd_vdac_load([128]) → 2-byte length, payload 1 byte")
    b = cmd_vdac_load([128])
    # [BB][06][len_hi=0][len_lo=1][128]
    if b != bytes([0xBB, 0x06, 0x00, 0x01, 128]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    r = Result("cmd_vdac_load(200 bytes) — len_hi/lo correcto")
    seq = list(range(200))
    b = cmd_vdac_load(seq)
    if b[0] != 0xBB or b[1] != 0x06:
        r.fail(f"Marcador/cmd incorrecto: {b[:4].hex()}")
    n = (b[2] << 8) | b[3]
    if n != 200: r.fail(f"len={n} esperado 200")
    if list(b[4:]) != seq[:200]: r.fail("payload incorrecto")
    results.append(r)

    r = Result("cmd_vdac_load máximo 3000 bytes — no excede")
    seq = list(range(256)) * 12   # 3072 items
    b = cmd_vdac_load(seq)
    n = (b[2] << 8) | b[3]
    if n != 3000: r.fail(f"len={n} esperado 3000 (clamped)")
    results.append(r)

    # ── cmd_set_debug ─────────────────────────────────────────────────────────
    r = Result("cmd_set_debug(True) → [BB 07 01 01]")
    b = cmd_set_debug(True)
    if b != bytes([0xBB, 0x07, 0x01, 0x01]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── cmd_set_debug_ch ─────────────────────────────────────────────────────
    r = Result("cmd_set_debug_ch(2) → [BB 08 01 02]")
    b = cmd_set_debug_ch(2)
    if b != bytes([0xBB, 0x08, 0x01, 0x02]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── cmd_get_state ─────────────────────────────────────────────────────────
    r = Result("cmd_get_state → [BB 09 00]")
    b = cmd_get_state()
    if b != bytes([0xBB, 0x09, 0x00]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── cmd_fir_clear ─────────────────────────────────────────────────────────
    r = Result("cmd_fir_clear → [BB 0B 00]")
    b = cmd_fir_clear()
    if b != bytes([0xBB, 0x0B, 0x00]):
        r.fail(f"Got: {b.hex()}")
    results.append(r)

    # ── cmd_load_fir — estructura del frame ──────────────────────────────────
    r = Result("cmd_load_fir([1.0]) → [BB 0A 00 02 FF 7F] (1 tap @ 32767)")
    b = cmd_load_fir([1.0])
    if b[0] != 0xBB or b[1] != 0x0A:
        r.fail(f"Marcador/cmd incorrecto: {b[:4].hex()}")
    plen = (b[2] << 8) | b[3]
    if plen != 2: r.fail(f"plen={plen} esperado 2")
    coeff = struct.unpack_from('<h', b, 4)[0]
    if coeff != 32767: r.fail(f"coeff={coeff} esperado 32767")
    results.append(r)

    r = Result("cmd_load_fir([-1.0]) → coeff = -32768 (min int16)")
    b = cmd_load_fir([-1.0])
    coeff = struct.unpack_from('<h', b, 4)[0]
    if coeff != -32768: r.fail(f"coeff={coeff} esperado -32768")
    results.append(r)

    r = Result("cmd_load_fir([0.5] * 63) — plen = 126 bytes (63 taps × 2)")
    b = cmd_load_fir([0.5] * 63)
    plen = (b[2] << 8) | b[3]
    if plen != 126: r.fail(f"plen={plen} esperado 126")
    if len(b) != 4 + 126: r.fail(f"total={len(b)} esperado 130")
    # Todos los coeficientes deben ser ~16383 (0.5 × 32767 = 16383.5 → round 16384)
    for i in range(63):
        c = struct.unpack_from('<h', b, 4 + i * 2)[0]
        if abs(c - 16384) > 1:
            r.fail(f"tap {i}: coeff={c} esperado ~16384")
            break
    results.append(r)

    r = Result("cmd_load_fir(256 taps) → truncado a 255 (límite firmware)")
    b = cmd_load_fir([0.001] * 256)
    plen = (b[2] << 8) | b[3]
    if plen != 255 * 2: r.fail(f"plen={plen} esperado {255*2} (255 taps clamped)")
    results.append(r)

    r = Result("cmd_load_fir([]) → devuelve cmd_fir_clear [BB 0B 00]")
    b = cmd_load_fir([])
    if b != bytes([0xBB, 0x0B, 0x00]):
        r.fail(f"Lista vacía debería devolver FIR_CLEAR, got: {b.hex()}")
    results.append(r)

    # ── Verificación Q1.15 para un filtro real ────────────────────────────────
    r = Result("Q1.15 encoding: suma de coeficientes LP → DC gain ≈ 1.0")
    from digital_filters import design_fir_lowpass
    coeffs = design_fir_lowpass(100.0, 1500.0, 63)
    b = cmd_load_fir(coeffs)
    # Reconstruir los coeficientes Q1.15 y verificar DC gain
    q15_sum = 0
    for i in range(63):
        c = struct.unpack_from('<h', b, 4 + i * 2)[0]
        q15_sum += c
    dc_gain = q15_sum / 32767.0
    if abs(dc_gain - 1.0) > 0.05:   # tolerancia 5%
        r.fail(f"DC gain = {dc_gain:.4f} (esperado ≈ 1.0)")
    results.append(r)

    return results


def main():
    print("=" * 60)
    print("  TEST PROTOCOL BUILDERS")
    print("=" * 60)
    results = run()
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    for r in results: print(r)
    print("─" * 60)
    print(f"  Total: {len(results)}  |  PASS: {passed}  |  FAIL: {failed}")
    print("=" * 60)
    return failed == 0


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
