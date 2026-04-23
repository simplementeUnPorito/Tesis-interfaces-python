#!/usr/bin/env python3
"""
test_parser_adversarial.py — Parser robustness under hostile byte streams.

Simulates the exact parsing loop from serial_worker.py with adversarial input:
  1. Garbage bytes before first valid packet
  2. 0xAA byte embedded in CRC-valid 0xCC state packet
  3. Truncated 0xAA packet followed by a valid one
  4. 0xDD debug event with large corrupted plen (guard test)
  5. Two valid 0xAA packets back-to-back — no bytes lost
  6. Interleaved 0xAA + 0xCC + 0xDD in one byte stream
  7. 0xFF × 100 garbage then valid 0xAA packet — recovered
  8. Repeated 0xAA + 0xCC + 0xDD × 50 rounds — zero bad packets
  9. 0xDD with plen=33 (just over guard threshold) → dropped
 10. 0xDD with plen=0 (valid zero-payload event) → parsed OK
"""

import sys
import os
import struct
import math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol      import (PKT_DATA_HEADER, PKT_DATA_SIZE, parse_packet,
                            SAR_FULLSCALE, SAR_VREF)
from debug_protocol import (DBG_HEADER, STATE_HEADER, STATE_PKT_LEN,
                             parse_debug_packet, parse_state_packet)


# ── Packet builders ────────────────────────────────────────────────────────────

def _xor(data: bytes) -> int:
    c = 0
    for b in data: c ^= b
    return c


def make_data_pkt(raw_input: int = 512, analog: int = 0, digital: int = 0,
                  cfg: int = 4, buf_gain: int = 8) -> bytes:
    gc = {1: 0, 2: 1, 4: 2, 8: 3}.get(buf_gain, 3)
    gain_byte = (gc << 4) | (cfg & 0x0F)
    raw_b = struct.pack('<h', raw_input)
    def to3(v):
        v24 = v & 0xFFFFFF
        return bytes([v24 & 0xFF, (v24 >> 8) & 0xFF, (v24 >> 16) & 0xFF])
    body = bytes([PKT_DATA_HEADER, 0]) + raw_b + to3(analog) + to3(digital) + bytes([gain_byte])
    return body + bytes([_xor(body)])


def make_state_pkt(adc_cfg=4, buf_gain=8, streaming=1) -> bytes:
    raw = bytes([STATE_HEADER, 7, adc_cfg, buf_gain, streaming, 0, 0, 0, 0])
    return raw + bytes([_xor(raw)])


def make_debug_pkt(event: int, payload: bytes = b'') -> bytes:
    body = bytes([DBG_HEADER, event, len(payload)]) + payload
    return body + bytes([_xor(body)])


# ── Parser (replicates serial_worker.py parse loop logic) ─────────────────────

def parse_stream(stream: bytes):
    """Returns (n_data, n_state, n_debug, n_bad)."""
    buf = bytearray(stream)
    n_data = n_state = n_debug = n_bad = 0

    while buf:
        if buf[0] == PKT_DATA_HEADER:
            if len(buf) < PKT_DATA_SIZE:
                break
            pkt = parse_packet(bytes(buf[:PKT_DATA_SIZE]))
            if pkt is not None:
                n_data += 1
                del buf[:PKT_DATA_SIZE]
            else:
                n_bad += 1
                del buf[:1]

        elif buf[0] == STATE_HEADER:
            if len(buf) < STATE_PKT_LEN:
                break
            st = parse_state_packet(bytes(buf[:STATE_PKT_LEN]))
            if st is not None:
                n_state += 1
                del buf[:STATE_PKT_LEN]
            else:
                n_bad += 1
                del buf[:1]

        elif buf[0] == DBG_HEADER:
            if len(buf) < 4:
                break
            plen = buf[2]
            if plen > 32:
                n_bad += 1
                del buf[:1]
                continue
            pkt_size = 4 + plen
            if len(buf) < pkt_size:
                break
            dbg = parse_debug_packet(bytes(buf[:pkt_size]))
            if dbg is not None:
                n_debug += 1
                del buf[:pkt_size]
            else:
                n_bad += 1
                del buf[:1]

        else:
            del buf[:1]   # garbage byte

    return n_data, n_state, n_debug, n_bad


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

    # ── 1. Garbage antes del primer paquete ──────────────────────────────────
    r = Result("Garbage (100×0xFF) antes del primer 0xAA válido → 1 data, 0 bad")
    stream = bytes([0xFF] * 100) + make_data_pkt()
    d, s, dbg, bad = parse_stream(stream)
    if d != 1:   r.fail(f"n_data={d}")
    if bad != 0: r.fail(f"n_bad={bad} (garbage no debe contar como bad)")
    results.append(r)

    # ── 2. 0xAA en el payload de 0xCC — no confunde al parser ────────────────
    r = Result("0xAA en payload 0xCC → parser recupera y parsea ambos")
    # Build state packet with 0xAA in one of the data fields
    # We use adc_cfg=0xAA which triggers this case
    raw9 = bytes([STATE_HEADER, 7, 0xAA, 8, 1, 0, 0, 0, 0])
    crc  = _xor(raw9)
    state_with_AA = raw9 + bytes([crc])
    data_pkt = make_data_pkt()
    stream = state_with_AA + data_pkt
    d, s, dbg, bad = parse_stream(stream)
    if s != 1: r.fail(f"n_state={s} esperado 1")
    if d != 1: r.fail(f"n_data={d} esperado 1")
    results.append(r)

    # ── 3. Paquete 0xAA truncado + paquete 0xAA válido ───────────────────────
    r = Result("0xAA truncado (6 bytes) + válido → se parsea solo el válido")
    valid = make_data_pkt(raw_input=1024)
    stream = valid[:6] + valid   # first 6 bytes of valid then full valid
    # The truncated bytes will be consumed as garbage until the second 0xAA
    d, s, dbg, bad = parse_stream(stream)
    # After truncated start: parser sees 0xAA, tries to parse 12 bytes → partial
    # overlap structure means result depends on whether the 7-byte tail forms a valid CRC
    # What matters: at least 1 valid packet is found and parser doesn't crash
    if d < 1:
        r.fail(f"n_data={d} — debería parsear al menos 1 paquete válido")
    results.append(r)

    # ── 4. 0xDD con plen grande (guard: >32) → descartado como bad byte ──────
    r = Result("0xDD con plen=200 → byte descartado (large plen guard), parser continúa")
    large_plen_pkt = bytes([DBG_HEADER, 0x20, 200])  # plen=200 >> 32
    valid_data = make_data_pkt()
    stream = large_plen_pkt + valid_data
    d, s, dbg, bad = parse_stream(stream)
    if d < 1:    r.fail(f"Parser no recuperó paquete válido después de plen grande: d={d}")
    # The 0xDD header byte should be counted as bad
    if bad < 1:  r.fail(f"n_bad={bad} — el 0xDD con plen grande debe contar como bad")
    results.append(r)

    # ── 5. Dos 0xAA válidos consecutivos — sin bytes perdidos ────────────────
    r = Result("Dos 0xAA consecutivos → n_data=2, n_bad=0")
    stream = make_data_pkt(raw_input=100) + make_data_pkt(raw_input=200)
    d, s, dbg, bad = parse_stream(stream)
    if d != 2:   r.fail(f"n_data={d} esperado 2")
    if bad != 0: r.fail(f"n_bad={bad}")
    results.append(r)

    # ── 6. Interleaved 0xAA + 0xCC + 0xDD ───────────────────────────────────
    r = Result("Interleaved 0xAA + 0xCC + 0xDD → se parsean los 3 tipos")
    stream = (make_data_pkt() +
              make_state_pkt() +
              make_debug_pkt(0x20) +   # Streaming ON
              make_data_pkt(raw_input=500))
    d, s, dbg, bad = parse_stream(stream)
    if d != 2:    r.fail(f"n_data={d} esperado 2")
    if s != 1:    r.fail(f"n_state={s} esperado 1")
    if dbg != 1:  r.fail(f"n_debug={dbg} esperado 1")
    if bad != 0:  r.fail(f"n_bad={bad}")
    results.append(r)

    # ── 7. 100×0xFF garbage + válido → recuperado ────────────────────────────
    r = Result("100×0xFF + paquete válido → parser recupera correctamente")
    stream = bytes([0xFF] * 100) + make_data_pkt()
    d, s, dbg, bad = parse_stream(stream)
    if d != 1: r.fail(f"n_data={d} esperado 1")
    results.append(r)

    # ── 8. 50 rondas de 0xAA + 0xCC + 0xDD — cero bad ───────────────────────
    r = Result("50 rondas de (0xAA + 0xCC + 0xDD) → 150 paquetes, 0 bad")
    stream = b''
    for i in range(50):
        stream += make_data_pkt(raw_input=i*10)
        stream += make_state_pkt()
        stream += make_debug_pkt(0x30, bytes([0x01]))   # Command received: START
    d, s, dbg, bad = parse_stream(stream)
    if d != 50:    r.fail(f"n_data={d} esperado 50")
    if s != 50:    r.fail(f"n_state={s} esperado 50")
    if dbg != 50:  r.fail(f"n_debug={dbg} esperado 50")
    if bad != 0:   r.fail(f"n_bad={bad}")
    results.append(r)

    # ── 9. 0xDD con plen=33 (justo sobre guard 32) → descartado ──────────────
    r = Result("0xDD plen=33 (≥ guard threshold 32) → 0xDD header descartado")
    stream = bytes([DBG_HEADER, 0x20, 33]) + make_data_pkt()
    d, s, dbg, bad = parse_stream(stream)
    if bad < 1: r.fail(f"n_bad={bad} — plen=33 debe rechazarse")
    if d < 1:   r.fail(f"Parser no recuperó tras plen=33 oversized")
    results.append(r)

    # ── 10. 0xDD con plen=0 (evento sin payload) → OK ────────────────────────
    r = Result("0xDD plen=0 (Streaming OFF sin payload) → parseado OK")
    pkt = make_debug_pkt(0x21, b'')   # Streaming OFF, no payload
    d, s, dbg, bad = parse_stream(pkt)
    if dbg != 1: r.fail(f"n_debug={dbg} esperado 1")
    if bad != 0: r.fail(f"n_bad={bad}")
    results.append(r)

    return results


def main():
    print("=" * 62)
    print("  TEST PARSER ADVERSARIAL — Robustez bajo byte streams hostiles")
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
