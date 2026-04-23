#!/usr/bin/env python3
"""
test_channel_filter.py — Unit tests for ChannelFilter real-time block processing.

Verifies:
  1. No filter loaded → process() returns copy of input unchanged
  2. FIR lowpass: passes 10 Hz, attenuates 200 Hz over a full-length signal
  3. IIR notch (set_notch): 60 Hz attenuated ≥ 20 dB
  4. IIR Butterworth lowpass: block-by-block processing matches full signal
  5. State persists across block boundaries — no click at block join
  6. reset() clears state (startup transient resets)
  7. clear() reverts to pass-through
  8. set_bandpass: passes 40-80 Hz, attenuates 10 Hz and 200 Hz
  9. set_highpass: attenuates DC / low freq, passes high freq
 10. process() with empty array → returns empty array (no crash)
 11. Active property true after filter loaded, false after clear()
"""

import sys
import os
import math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from digital_filters import (ChannelFilter, make_notch, make_bandpass,
                              make_highpass, design_fir_lowpass)


FS    = 1500.0
DUR   = 2.0    # seconds


def gen_tone(freq_hz: float, duration_s: float = DUR, fs: float = FS) -> np.ndarray:
    t = np.arange(int(fs * duration_s)) / fs
    return np.sin(2.0 * math.pi * freq_hz * t).astype(np.float64)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2)))


def power_ratio_db(y_ref: np.ndarray, y_filt: np.ndarray) -> float:
    p_ref  = np.mean(y_ref  ** 2)
    p_filt = np.mean(y_filt ** 2)
    if p_ref == 0 or p_filt == 0:
        return -200.0
    return 10.0 * math.log10(p_filt / p_ref)


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

    # ── 1. Sin filtro → pass-through ─────────────────────────────────────────
    r = Result("Sin filtro cargado → process() devuelve copia exacta de la entrada")
    f = ChannelFilter()
    x = gen_tone(10.0, duration_s=0.1)
    y = f.process(x)
    if not np.array_equal(x, y):
        r.fail("La salida difiere de la entrada sin filtro cargado")
    if y is x:
        r.fail("process() devolvió la misma referencia (debería devolver copia)")
    results.append(r)

    # ── 2. FIR lowpass: pasa 10 Hz, atenúa 200 Hz ────────────────────────────
    r = Result("FIR pasa-bajos 50 Hz: pasa 10 Hz (≥-3 dB), atenúa 200 Hz (≥20 dB)")
    f = ChannelFilter()
    h = design_fir_lowpass(50.0, FS, 63)
    f.set_fir(h)
    x10  = gen_tone(10.0)
    x200 = gen_tone(200.0)
    y10  = f.process(x10.copy());   f.reset()
    y200 = f.process(x200.copy())
    skip = len(h)   # skip group-delay transient
    g10  = power_ratio_db(x10[skip:],  y10[skip:])
    g200 = power_ratio_db(x200[skip:], y200[skip:])
    if g10 < -3.0:
        r.fail(f"Ganancia 10 Hz = {g10:.1f} dB (mínimo -3 dB)")
    if g200 > -20.0:
        r.fail(f"Ganancia 200 Hz = {g200:.1f} dB (máximo -20 dB)")
    results.append(r)

    # ── 3. IIR notch 60 Hz — atenúa ≥ 20 dB ─────────────────────────────────
    r = Result("IIR notch 60 Hz (Q=30): atenúa ≥ 20 dB @ 60 Hz")
    f = ChannelFilter()
    f.set_notch(60.0, Q=30.0, fs=FS)
    x60 = gen_tone(60.0)
    y60 = f.process(x60.copy())
    # Q=30 → ring-down ≈ Q/(π·f) ≈ 0.16 s; skip 0.3 s to fully settle
    skip = int(FS * 0.3)
    g60 = power_ratio_db(x60[skip:], y60[skip:])
    if g60 > -20.0:
        r.fail(f"Ganancia 60 Hz = {g60:.1f} dB (máximo -20 dB)")
    results.append(r)

    # ── 4. Procesamiento en bloques = procesamiento de una sola pasada ────────
    r = Result("Butterworth LP 100 Hz: bloques de 30 == procesamiento único")
    f_single = ChannelFilter()
    f_single.set_lowpass(100.0, FS, order=4)
    f_blocks = ChannelFilter()
    f_blocks.set_lowpass(100.0, FS, order=4)

    sig = gen_tone(10.0) + 0.5 * gen_tone(200.0)
    y_single = f_single.process(sig.copy())

    BLOCK = 30
    y_blocks = np.empty_like(sig)
    for i in range(0, len(sig), BLOCK):
        blk = sig[i:i + BLOCK]
        y_blocks[i:i + BLOCK] = f_blocks.process(blk.copy())

    max_diff = float(np.max(np.abs(y_single - y_blocks)))
    if max_diff > 1e-10:
        r.fail(f"Diferencia máxima entre bloque-a-bloque y pasada única: {max_diff:.2e}")
    results.append(r)

    # ── 5. Estado persiste entre bloques — sin click en la unión ─────────────
    r = Result("Estado persiste entre bloques — no hay discontinuidad en la unión")
    f = ChannelFilter()
    f.set_lowpass(100.0, FS, order=4)

    tone = gen_tone(10.0, duration_s=1.0)
    mid  = len(tone) // 2

    # Process as two halves
    y1 = f.process(tone[:mid].copy())
    y2 = f.process(tone[mid:].copy())
    y_split = np.concatenate([y1, y2])

    # Process as one block
    f.reset()
    y_full = f.process(tone.copy())

    diff = float(np.max(np.abs(y_split - y_full)))
    if diff > 1e-10:
        r.fail(f"Discontinuidad en la unión de bloques: diff={diff:.2e}")
    results.append(r)

    # ── 6. reset() limpia el estado ───────────────────────────────────────────
    r = Result("reset() limpia el estado — el filtro vuelve a comportarse como al inicio")
    f = ChannelFilter()
    f.set_lowpass(100.0, FS, order=4)

    # Process a burst to load state
    noise = np.random.default_rng(42).random(300) * 2 - 1
    _ = f.process(noise)

    # Now process same tone twice: once with loaded state, once after reset
    tone = gen_tone(10.0, duration_s=0.2)

    y_loaded = f.process(tone.copy())
    f.reset()
    y_reset  = f.process(tone.copy())

    # After reset, behavior at start should differ from loaded-state start
    # but after both settle (skip transient), they should converge
    settle = 200
    diff_settled = float(np.max(np.abs(y_loaded[settle:] - y_reset[settle:])))
    if diff_settled > 1e-6:
        r.fail(f"Los outputs no convergen tras reset: diff_settled={diff_settled:.2e}")
    results.append(r)

    # ── 7. clear() revierte a pass-through ───────────────────────────────────
    r = Result("clear() → active=False, process() devuelve copia exacta")
    f = ChannelFilter()
    f.set_lowpass(100.0, FS)
    if not f.active: r.fail("active debería ser True después de set_lowpass")
    f.clear()
    if f.active: r.fail("active debería ser False después de clear()")
    x = gen_tone(200.0, duration_s=0.1)
    y = f.process(x.copy())
    if not np.array_equal(x, y):
        r.fail("Después de clear(), process() modifica la señal")
    results.append(r)

    # ── 8. Bandpass 40-80 Hz ──────────────────────────────────────────────────
    r = Result("set_bandpass(40-80 Hz): pasa 60 Hz, atenúa 10 Hz y 200 Hz")
    f = ChannelFilter()
    f.set_bandpass(40.0, 80.0, FS, order=4)
    skip = int(FS * 0.3)   # longer transient for bandpass

    x10  = gen_tone(10.0)
    x60  = gen_tone(60.0)
    x200 = gen_tone(200.0)
    y10  = f.process(x10.copy());   f.reset()
    y60  = f.process(x60.copy());   f.reset()
    y200 = f.process(x200.copy())

    g10  = power_ratio_db(x10[skip:],  y10[skip:])
    g60  = power_ratio_db(x60[skip:],  y60[skip:])
    g200 = power_ratio_db(x200[skip:], y200[skip:])

    if g60 < -3.0:
        r.fail(f"60 Hz = {g60:.1f} dB (mínimo -3 dB en banda de paso)")
    if g10 > -20.0:
        r.fail(f"10 Hz = {g10:.1f} dB (máximo -20 dB fuera de banda)")
    if g200 > -20.0:
        r.fail(f"200 Hz = {g200:.1f} dB (máximo -20 dB fuera de banda)")
    results.append(r)

    # ── 9. Highpass ───────────────────────────────────────────────────────────
    r = Result("set_highpass(200 Hz): atenúa 10 Hz (≤-20 dB), pasa 400 Hz (≥-3 dB)")
    f = ChannelFilter()
    f.set_highpass(200.0, FS, order=4)
    skip = int(FS * 0.2)

    x10  = gen_tone(10.0)
    x400 = gen_tone(400.0)
    y10  = f.process(x10.copy());   f.reset()
    y400 = f.process(x400.copy())

    g10  = power_ratio_db(x10[skip:],  y10[skip:])
    g400 = power_ratio_db(x400[skip:], y400[skip:])

    if g10 > -20.0:
        r.fail(f"10 Hz = {g10:.1f} dB (máximo -20 dB)")
    if g400 < -3.0:
        r.fail(f"400 Hz = {g400:.1f} dB (mínimo -3 dB en paso)")
    results.append(r)

    # ── 10. process() con array vacío → sin crash ────────────────────────────
    r = Result("process(empty array) → devuelve array vacío sin crash")
    f = ChannelFilter()
    f.set_lowpass(100.0, FS)
    try:
        y = f.process(np.array([], dtype=float))
        if len(y) != 0:
            r.fail(f"Se esperaba array vacío, got len={len(y)}")
    except Exception as e:
        r.fail(f"Excepción: {e}")
    results.append(r)

    # ── 11. active y mode correcto en cada tipo ───────────────────────────────
    r = Result("active/mode correcto: 'none' → 'fir' → 'iir' → 'none'")
    f = ChannelFilter()
    if f.active or f.mode != 'none':
        r.fail(f"Initial: active={f.active} mode={f.mode}")
    f.set_fir(design_fir_lowpass(50.0, FS, 31))
    if not f.active or f.mode != 'fir':
        r.fail(f"After set_fir: active={f.active} mode={f.mode}")
    f.set_notch(60.0, 30.0, FS)
    if not f.active or f.mode != 'iir':
        r.fail(f"After set_notch: active={f.active} mode={f.mode}")
    f.clear()
    if f.active or f.mode != 'none':
        r.fail(f"After clear: active={f.active} mode={f.mode}")
    results.append(r)

    return results


def main():
    print("=" * 62)
    print("  TEST CHANNEL FILTER — Procesamiento en tiempo real")
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
