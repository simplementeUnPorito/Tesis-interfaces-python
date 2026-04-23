#!/usr/bin/env python3
"""
test_parse_packet.py — Pruebas unitarias de parse_packet y effective_fullscale.

Verifica la conversión ADC→voltios para todos los CFG y ganancias,
y casos borde del parser de paquetes 0xAA.
"""

import sys
import os
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol import (parse_packet, parse_gain_byte, effective_fullscale,
                      PKT_DATA_HEADER, PKT_DATA_SIZE,
                      CONFIG_BASE_RANGE, CONFIG_BASE_GAIN,
                      SAR_VREF, SAR_FULLSCALE, ADC_FULLSCALE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _xor(data: bytes) -> int:
    c = 0
    for b in data: c ^= b
    return c


def _sign24_py(v: int) -> int:
    return v - 0x1000000 if (v & 0x800000) else v


def make_data_pkt(raw_input: int, post_analog: int, post_digital: int,
                  cfg: int = 4, buf_gain: int = 8, mux_in: int = 0,
                  mux_out: int = 0, corrupt_crc: bool = False) -> bytes:
    """Build a 12-byte 0xAA data packet with exact values."""
    flags = (mux_in & 0x01) | ((mux_out & 0x01) << 1)
    gc = {1: 0, 2: 1, 4: 2, 8: 3}.get(buf_gain, 3)
    gain_byte = (gc << 4) | (cfg & 0x0F)

    raw_bytes  = struct.pack('<h', raw_input)
    # 3-byte LE for 24-bit values (post_analog, post_digital)
    def to3(v):
        v24 = v & 0xFFFFFF
        return bytes([v24 & 0xFF, (v24 >> 8) & 0xFF, (v24 >> 16) & 0xFF])

    body = bytes([PKT_DATA_HEADER, flags]) + raw_bytes + to3(post_analog) + to3(post_digital) + bytes([gain_byte])
    crc  = _xor(body)
    if corrupt_crc: crc ^= 0xFF
    return body + bytes([crc])


class Result:
    def __init__(self, name): self.name = name; self.ok = True; self.errors = []
    def fail(self, m): self.ok = False; self.errors.append(m)
    def __str__(self):
        s = "PASS ✓" if self.ok else "FAIL ✗"
        lines = [f"  [{s}] {self.name}"]
        for e in self.errors: lines.append(f"         → {e}")
        return "\n".join(lines)


# ── Escenarios ────────────────────────────────────────────────────────────────

def run():
    results = []

    # ── effective_fullscale para cada cfg ─────────────────────────────────────
    r = Result("effective_fullscale CFG1/bg=1 → ±0.512 V exacto")
    vfs = effective_fullscale(1, 1)
    if abs(vfs - 0.512) > 1e-9: r.fail(f"vfs={vfs}")
    results.append(r)

    r = Result("effective_fullscale CFG2/bg=8 → ±1.024 V exacto")
    vfs = effective_fullscale(2, 8)
    if abs(vfs - 1.024) > 1e-9: r.fail(f"vfs={vfs}")
    results.append(r)

    r = Result("effective_fullscale CFG2/bg=4 → ±2.048 V (doble del rango)")
    vfs = effective_fullscale(2, 4)
    if abs(vfs - 2.048) > 1e-9: r.fail(f"vfs={vfs}")
    results.append(r)

    r = Result("effective_fullscale CFG4/bg=8 → ±6.144 V")
    vfs = effective_fullscale(4, 8)
    if abs(vfs - 6.144) > 1e-9: r.fail(f"vfs={vfs}")
    results.append(r)

    r = Result("effective_fullscale cfg=0 (inválido) → fallback 6.144 × 8/1 = 49.152")
    vfs = effective_fullscale(0, 1)
    if abs(vfs - 49.152) > 1e-9: r.fail(f"vfs={vfs}")
    results.append(r)

    r = Result("effective_fullscale buf_gain=0 → no ZeroDivision (protected)")
    try:
        vfs = effective_fullscale(4, 0)
        # max(buf_gain, 1) → 1: same as buf_gain=1
        expected = effective_fullscale(4, 1)
        if abs(vfs - expected) > 1e-9: r.fail(f"vfs={vfs} esperado {expected}")
    except ZeroDivisionError:
        r.fail("ZeroDivisionError con buf_gain=0")
    results.append(r)

    # ── parse_gain_byte ───────────────────────────────────────────────────────
    r = Result("parse_gain_byte(0x34) → cfg=4, buf_gain=8")
    cfg, bg = parse_gain_byte(0x34)
    if cfg != 4: r.fail(f"cfg={cfg}")
    if bg != 8:  r.fail(f"buf_gain={bg}")
    results.append(r)

    r = Result("parse_gain_byte(0x21) → cfg=1, buf_gain=4")
    cfg, bg = parse_gain_byte(0x21)
    if cfg != 1: r.fail(f"cfg={cfg}")
    if bg != 4:  r.fail(f"buf_gain={bg}")
    results.append(r)

    r = Result("parse_gain_byte(0x00) → cfg=0, buf_gain=1 (min)")
    cfg, bg = parse_gain_byte(0x00)
    if cfg != 0: r.fail(f"cfg={cfg}")
    if bg != 1:  r.fail(f"buf_gain={bg}")
    results.append(r)

    # ── parse_packet — happy path ─────────────────────────────────────────────
    r = Result("parse_packet — SAR=1024 → raw_input ≈ +0.512 V")
    pkt_bytes = make_data_pkt(raw_input=1024, post_analog=0, post_digital=0)
    pkt = parse_packet(pkt_bytes)
    if pkt is None: r.fail("None")
    else:
        expected_v = 1024 / SAR_FULLSCALE * SAR_VREF
        if abs(pkt['raw_input'] - expected_v) > 1e-6:
            r.fail(f"raw_input={pkt['raw_input']:.6f} esperado {expected_v:.6f}")
    results.append(r)

    r = Result("parse_packet — SAR=-2048 → raw_input = -1.024 V (min int12)")
    pkt_bytes = make_data_pkt(raw_input=-2048, post_analog=0, post_digital=0)
    pkt = parse_packet(pkt_bytes)
    if pkt is None: r.fail("None")
    else:
        expected_v = -2048 / SAR_FULLSCALE * SAR_VREF
        if abs(pkt['raw_input'] - expected_v) > 1e-6:
            r.fail(f"raw_input={pkt['raw_input']:.6f} esperado {expected_v:.6f}")
    results.append(r)

    r = Result("parse_packet — post_analog = max int24 (0x7FFFFF) → voltaje positivo máximo")
    MAX24 = 0x7FFFFF
    pkt_bytes = make_data_pkt(raw_input=0, post_analog=MAX24, post_digital=0)
    pkt = parse_packet(pkt_bytes)
    if pkt is None: r.fail("None")
    elif pkt['post_analog'] <= 0:
        r.fail(f"post_analog debería ser positivo: {pkt['post_analog']:.4f}")
    results.append(r)

    r = Result("parse_packet — post_digital = min int24 (0x800000) → voltaje negativo mínimo")
    MIN24 = 0x800000   # -8388608 after sign extension
    pkt_bytes = make_data_pkt(raw_input=0, post_analog=0, post_digital=MIN24)
    pkt = parse_packet(pkt_bytes)
    if pkt is None: r.fail("None")
    elif pkt['post_digital'] >= 0:
        r.fail(f"post_digital debería ser negativo: {pkt['post_digital']:.4f}")
    results.append(r)

    r = Result("parse_packet — CRC corrompido → None")
    pkt_bytes = make_data_pkt(0, 0, 0, corrupt_crc=True)
    if parse_packet(pkt_bytes) is not None:
        r.fail("No rechazó CRC incorrecto")
    results.append(r)

    r = Result("parse_packet — marcador incorrecto → None")
    pkt_bytes = bytearray(make_data_pkt(0, 0, 0))
    pkt_bytes[0] = 0xBB
    if parse_packet(bytes(pkt_bytes)) is not None:
        r.fail("No rechazó marcador incorrecto")
    results.append(r)

    r = Result("parse_packet — datos < PKT_DATA_SIZE → None")
    if parse_packet(bytes([0xAA] * 5)) is not None:
        r.fail("No rechazó paquete demasiado corto")
    results.append(r)

    # ── Todos los CFG × ganancias válidos → parse OK sin error ───────────────
    r = Result("parse_packet — todos los CFG (1-4) × ganancias (1/2/4/8) → OK")
    for cfg in [1, 2, 3, 4]:
        for bg in [1, 2, 4, 8]:
            if cfg == 1 and bg != 1:
                continue   # CFG1 solo acepta bg=1 (forzado por firmware)
            pkt_bytes = make_data_pkt(512, 1000, -500, cfg=cfg, buf_gain=bg)
            pkt = parse_packet(pkt_bytes)
            if pkt is None:
                r.fail(f"None para cfg={cfg} bg={bg}")
                break
            if pkt['adc_cfg'] != cfg:
                r.fail(f"adc_cfg={pkt['adc_cfg']} esperado {cfg}")
                break
            if pkt['buf_gain'] != bg:
                r.fail(f"buf_gain={pkt['buf_gain']} esperado {bg}")
                break
    results.append(r)

    # ── mux_in=1 (VDAC mode) — conversión de raw_input vía vfs ───────────────
    r = Result("parse_packet — mux_in=1 (VDAC) raw_input=64 → raw_input_v = +0.5 × vfs")
    vfs_expected = effective_fullscale(4, 8)   # 6.144
    pkt_bytes = make_data_pkt(64, 0, 0, cfg=4, buf_gain=8, mux_in=1)
    pkt = parse_packet(pkt_bytes)
    if pkt is None: r.fail("None")
    else:
        expected_v = 64 / 128.0 * vfs_expected
        if abs(pkt['raw_input'] - expected_v) > 1e-6:
            r.fail(f"raw_input={pkt['raw_input']:.6f} esperado {expected_v:.6f}")
    results.append(r)

    # ── Verificación simétrica: +MAX24/2 ≈ +vfs/2 ────────────────────────────
    r = Result("parse_packet — post_analog = ADC_FULLSCALE/2 → ≈ vfs/2")
    half = ADC_FULLSCALE // 2
    pkt_bytes = make_data_pkt(0, half, 0, cfg=4, buf_gain=8)
    pkt = parse_packet(pkt_bytes)
    if pkt is None: r.fail("None")
    else:
        vfs = effective_fullscale(4, 8)
        expected = half / ADC_FULLSCALE * vfs
        if abs(pkt['post_analog'] - expected) > 0.001:
            r.fail(f"post_analog={pkt['post_analog']:.4f} esperado {expected:.4f}")
    results.append(r)

    return results


def main():
    print("=" * 60)
    print("  TEST PARSE PACKET + EFFECTIVE FULLSCALE")
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
