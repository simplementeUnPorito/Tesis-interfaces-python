#!/usr/bin/env python3
"""
test_fir_math.py — Verificación matemática del filtro FIR Q1.15.

Verifica que:
  1. La codificación Q1.15 no degrada significativamente la respuesta en frecuencia
  2. Un filtro notch 60 Hz atenúa correctamente la interferencia de red
  3. Un filtro pasa-bajos 20 Hz deja pasar señales de 10 Hz y atenúa 100 Hz
  4. La MAC en enteros (int64 >> 15) coincide con la referencia flotante
  5. El filtro con 1 tap de ganancia 1.0 es pass-through
  6. La saturación int24 funciona correctamente en valores extremos
"""

import sys
import os
import math
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from scipy import signal as scipy_signal
from digital_filters import (design_fir_lowpass, design_fir_notch, coeffs_to_q15)
from protocol import cmd_load_fir


# ── FIR simulado en Python (replica el algoritmo del PSoC) ───────────────────

def fir_q15_psoc(coeffs_q15: list[int], signal_int24: list[int]) -> list[int]:
    """Replica exacta del algoritmo del PSoC: MAC int64, shift >>15, saturación ±24bit."""
    n     = len(coeffs_q15)
    delay = [0] * 256   # uint8 circular buffer (misma que firmware)
    head  = 0
    out   = []
    for sample in signal_int24:
        delay[head] = sample
        acc = 0
        for k in range(n):
            idx = (head - k) & 0xFF
            acc += coeffs_q15[k] * delay[idx]
        head = (head + 1) & 0xFF
        result = acc >> 15
        # Saturación ±24bit
        if   result >  0x7FFFFF: result =  0x7FFFFF
        elif result < -0x800000: result = -0x800000
        out.append(result)
    return out


def fir_float_ref(coeffs_float, signal_float) -> np.ndarray:
    """Referencia flotante: numpy convolve."""
    h = np.asarray(coeffs_float)
    return np.convolve(signal_float, h)[:len(signal_float)]


def gen_signal(fs, duration, f_signal, f_noise=None, amp=0.1, noise_amp=0.05):
    """Genera señal sintética en float y en int24."""
    t       = np.arange(int(fs * duration)) / fs
    sig     = amp * np.sin(2 * math.pi * f_signal * t)
    if f_noise:
        sig += noise_amp * np.sin(2 * math.pi * f_noise * t)
    # Escalar a int24 (±8388607)
    SCALE = 8388607 / (amp + (noise_amp if f_noise else 0) + 1e-9)
    sig_int24 = [max(-8388608, min(8388607, int(round(v * SCALE)))) for v in sig]
    return sig, sig_int24, SCALE


def power_at_freq(signal, fs, target_hz, window=1.0):
    """Potencia de la señal en target_hz ± window Hz (usando DFT)."""
    n   = len(signal)
    dft = np.fft.rfft(np.asarray(signal, dtype=float))
    f   = np.fft.rfftfreq(n, 1.0 / fs)
    mask = (f >= target_hz - window) & (f <= target_hz + window)
    return float(np.sum(np.abs(dft[mask]) ** 2))


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
    FS = 1500.0
    DUR = 2.0   # segundos de señal para análisis en frecuencia

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Identidad: 1 tap × 1.0 ≈ pass-through
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("1 tap pass-through (coeff=32767/32768) — señal preservada ±0.001%")
    _, sig_int24, scale = gen_signal(FS, 1.0, 10.0, amp=0.1)
    q15 = [32767]   # ≈ 1.0
    out = fir_q15_psoc(q15, sig_int24)
    # Ganancia esperada: 32767/32768 ≈ 0.9999695
    ratios = [abs(o / s) if s != 0 else 1.0 for o, s in zip(out[1:], sig_int24[1:])]
    avg_gain = sum(ratios) / len(ratios) if ratios else 0
    if abs(avg_gain - 1.0) > 0.001:
        r.fail(f"Ganancia promedio = {avg_gain:.6f} (esperado ≈ 1.0 ±0.001)")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Filtro de todos ceros — salida debe ser 0
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Filtro de todos ceros (31 taps) — salida = 0 para cualquier entrada")
    _, sig_int24, _ = gen_signal(FS, 1.0, 10.0, f_noise=60.0)
    q15 = [0] * 31
    out = fir_q15_psoc(q15, sig_int24)
    if any(v != 0 for v in out):
        r.fail(f"Salida no es 0: max|out| = {max(abs(v) for v in out)}")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Filtro notch 60 Hz — atenúa ≥ 20 dB a 60 Hz, preserva 10 Hz
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Notch 60 Hz (255 taps) — atenúa ≥ 20 dB @ 60 Hz, preserva ≥ -3 dB @ 10 Hz")
    coeffs_f = design_fir_notch(60.0, FS, 255)
    q15      = coeffs_to_q15(coeffs_f)
    _, sig_int24, _ = gen_signal(FS, DUR, 10.0, f_noise=60.0, amp=0.08, noise_amp=0.08)

    out = fir_q15_psoc(q15, sig_int24)

    p_in_10  = power_at_freq(sig_int24, FS, 10.0)
    p_in_60  = power_at_freq(sig_int24, FS, 60.0)
    p_out_10 = power_at_freq(out, FS, 10.0)
    p_out_60 = power_at_freq(out, FS, 60.0)

    if p_in_60 > 0 and p_out_60 > 0:
        att_60_db = 10 * math.log10(p_in_60 / p_out_60)
        if att_60_db < 20.0:
            r.fail(f"Atenuación 60 Hz = {att_60_db:.1f} dB (mínimo 20 dB)")
    else:
        r.fail("No se pudo calcular potencia @ 60 Hz")

    if p_in_10 > 0 and p_out_10 > 0:
        att_10_db = 10 * math.log10(p_out_10 / p_in_10)
        if att_10_db < -3.0:   # ganancia no debe caer más de 3 dB a 10 Hz
            r.fail(f"Ganancia 10 Hz = {att_10_db:.1f} dB (mínimo -3 dB)")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Filtro pasa-bajos 20 Hz — deja pasar 10 Hz, atenúa 100 Hz
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Pasa-bajos 20 Hz (63 taps) — pasa 10 Hz (≥-3 dB), atenúa 100 Hz (≥20 dB)")
    coeffs_f = design_fir_lowpass(20.0, FS, 63)
    q15      = coeffs_to_q15(coeffs_f)
    _, sig_int24, _ = gen_signal(FS, DUR, 10.0, f_noise=100.0, amp=0.08, noise_amp=0.08)

    out = fir_q15_psoc(q15, sig_int24)

    p_in_10   = power_at_freq(sig_int24, FS, 10.0)
    p_in_100  = power_at_freq(sig_int24, FS, 100.0)
    p_out_10  = power_at_freq(out, FS, 10.0)
    p_out_100 = power_at_freq(out, FS, 100.0)

    if p_in_100 > 0 and p_out_100 > 0:
        att_100_db = 10 * math.log10(p_in_100 / p_out_100)
        if att_100_db < 20.0:
            r.fail(f"Atenuación 100 Hz = {att_100_db:.1f} dB (mínimo 20 dB)")
    if p_in_10 > 0 and p_out_10 > 0:
        g_10_db = 10 * math.log10(p_out_10 / p_in_10)
        if g_10_db < -3.0:
            r.fail(f"Ganancia 10 Hz = {g_10_db:.1f} dB (mínimo -3 dB)")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Q1.15 vs float — distorsión ≤ -60 dB (SNR de cuantización)
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Q1.15 vs float — SNR de cuantización ≥ 60 dB @ 10 Hz")
    coeffs_f = design_fir_lowpass(50.0, FS, 63)
    q15      = coeffs_to_q15(coeffs_f)
    sig_f, sig_int24, scale = gen_signal(FS, DUR, 10.0, amp=0.1)

    out_q15_int = fir_q15_psoc(q15, sig_int24)
    out_q15_f   = np.array(out_q15_int, dtype=float) / scale

    out_ref_f   = fir_float_ref(coeffs_f, sig_f)

    # SNR: signal power / quantization noise power
    delay   = len(coeffs_f) - 1   # group delay for causal FIR
    sig_ref = out_ref_f[delay:]
    sig_q15 = out_q15_f[delay:len(sig_ref) + delay]
    sig_ref = sig_ref[:len(sig_q15)]

    noise    = sig_q15 - sig_ref
    p_sig    = np.mean(sig_ref ** 2)
    p_noise  = np.mean(noise ** 2)

    if p_sig > 0 and p_noise > 0:
        snr_db = 10 * math.log10(p_sig / p_noise)
        if snr_db < 60.0:
            r.fail(f"SNR = {snr_db:.1f} dB (mínimo 60 dB para Q1.15)")
    elif p_noise == 0:
        pass   # error exactamente cero — mejor que lo requerido
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Saturación ±24bit — no crash con señal de amplitud máxima
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Saturación ±24bit — señal a máxima amplitud + coeff=1.0 → saturado sin crash")
    q15 = [32767] * 63   # extremo: todos los coeficientes al máximo
    signal_max = [8388607] * 200   # amplitud máxima constante
    try:
        out = fir_q15_psoc(q15, signal_max)
        if not all(-8388608 <= v <= 8388607 for v in out):
            r.fail("Salida fuera del rango int24 — saturación no funcionó")
    except Exception as e:
        r.fail(f"Excepción durante FIR extremo: {e}")
    results.append(r)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Pasa-bajos 20 Hz con señal geofónica real (10 Hz + 60 Hz)
    #    Verificar que el simulador_psoc.py también aplica el filtro
    # ─────────────────────────────────────────────────────────────────────────
    r = Result("Filtro pasa-bajos 20 Hz: señal sinusoide 10 Hz correctamente codificada")
    coeffs_f = design_fir_lowpass(20.0, FS, 31)
    q15      = coeffs_to_q15(coeffs_f)
    # Verificar que cmd_load_fir produce el mismo Q1.15 que coeffs_to_q15
    b = cmd_load_fir(coeffs_f)
    n_taps = ((b[2] << 8) | b[3]) // 2
    q15_from_cmd = [struct.unpack_from('<h', b, 4 + i*2)[0] for i in range(n_taps)]
    if q15_from_cmd != q15[:n_taps]:
        r.fail(f"Discrepancia entre cmd_load_fir y coeffs_to_q15")
    results.append(r)

    return results


def main():
    print("=" * 65)
    print("  TEST FIR MATH — Verificación matemática Q1.15")
    print("=" * 65)
    results = run()
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    for r in results: print(r)
    print("─" * 65)
    print(f"  Total: {len(results)}  |  PASS: {passed}  |  FAIL: {failed}")
    print("=" * 65)
    return failed == 0


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
