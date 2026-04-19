#!/usr/bin/env python3
"""
test_fir_simulador_integration.py — Verifica que el simulador aplica el FIR real.

Prueba que el campo post_digital del paquete 0xAA refleja la salida Q1.15 del FIR
cuando un filtro está cargado, y que revierte al valor sintético tras FIR_CLEAR.

Escenarios:
  1. Sin FIR → raw_digital ≠ raw_analog (generaciones sintéticas independientes)
  2. FIR identidad (1 tap ≈ 1.0) → raw_digital ≈ raw_analog (FIR aplica al analog_i)
  3. FIR todos-ceros → raw_digital ≈ 0 siempre
  4. FIR_CLEAR después de carga → raw_digital vuelve a ser sintético (distinto de analog)
"""

import sys
import os
import subprocess
import select
import time
import math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol import (parse_packet, cmd_start, cmd_stop,
                      cmd_load_fir, cmd_fir_clear,
                      PKT_DATA_HEADER, PKT_DATA_SIZE)


SIM_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'simulador_psoc.py')


def _start_sim() -> tuple:
    """Start simulator, wait for its PTY slave path, return (proc, slave_fd)."""
    proc = subprocess.Popen(
        [sys.executable, SIM_SCRIPT],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, text=True,
    )
    slave_path = None
    deadline   = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if 'Puerto virtual listo:' in line:
            slave_path = line.split(':', 1)[1].strip()
            break
    if not slave_path:
        proc.terminate()
        return None, None
    try:
        import tty
        fd = os.open(slave_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(fd)
    except OSError:
        proc.terminate()
        return None, None
    time.sleep(0.2)
    return proc, fd


def _stop_sim(proc, fd):
    if fd is not None:
        try: os.close(fd)
        except OSError: pass
    if proc is not None:
        proc.terminate()
        proc.wait(timeout=2)


def _write(fd, data: bytes):
    os.write(fd, data)


def _drain_pkts(fd: int, duration_s: float) -> list[dict]:
    """Drain parsed 0xAA data packets for duration_s seconds."""
    import errno as _errno
    deadline = time.monotonic() + duration_s
    buf  = bytearray()
    pkts = []
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                buf.extend(os.read(fd, 4096))
            except OSError as e:
                if e.errno == _errno.EAGAIN:
                    continue   # O_NONBLOCK race — retry
                break          # real error, exit
        while len(buf) >= PKT_DATA_SIZE:
            if buf[0] != PKT_DATA_HEADER:
                del buf[:1]
                continue
            pkt = parse_packet(bytes(buf[:PKT_DATA_SIZE]))
            if pkt is not None:
                pkts.append(pkt)
                del buf[:PKT_DATA_SIZE]
            else:
                del buf[:1]
    return pkts


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
    MIN_PKTS = 100

    # ── Escenario 1: sin FIR — raw_digital ≠ raw_analog ──────────────────────
    r = Result("Sin FIR: raw_digital ≠ raw_analog (señales sintéticas independientes)")
    proc, fd = _start_sim()
    if proc is None:
        r.fail("simulador no inició"); results.append(r)
    else:
        try:
            time.sleep(0.05)   # let simulator settle before first command
            _write(fd, cmd_start())
            pkts = _drain_pkts(fd, 0.5)
            _write(fd, cmd_stop())
            if len(pkts) < MIN_PKTS:
                r.fail(f"Pocos paquetes: {len(pkts)}")
            else:
                a = np.array([p['raw_analog']  for p in pkts], dtype=float)
                d = np.array([p['raw_digital'] for p in pkts], dtype=float)
                diff_rms = float(np.sqrt(np.mean((a - d) ** 2)))
                if diff_rms < 1.0:
                    r.fail(f"raw_digital ≈ raw_analog sin FIR (diff_rms={diff_rms:.1f})")
        except Exception as e:
            r.fail(f"Excepción: {e}")
        _stop_sim(proc, fd)
        results.append(r)

    # ── Escenario 2: FIR identidad → raw_digital ≈ raw_analog ────────────────
    r = Result("FIR identidad (1 tap=32767): raw_digital ≈ raw_analog (aplica analog_i)")
    proc, fd = _start_sim()
    if proc is None:
        r.fail("simulador no inició"); results.append(r)
    else:
        try:
            _write(fd, cmd_load_fir([32767.0 / 32768.0]))  # ≈ 1.0 en Q1.15
            time.sleep(0.05)
            _write(fd, cmd_start())
            pkts = _drain_pkts(fd, 0.5)
            _write(fd, cmd_stop())
            if len(pkts) < MIN_PKTS:
                r.fail(f"Pocos paquetes: {len(pkts)}")
            else:
                a = np.array([p['raw_analog']  for p in pkts], dtype=float)
                d = np.array([p['raw_digital'] for p in pkts], dtype=float)
                # FIR identity: digital_i = analog_i * (32767/32768), very high correlation
                nonzero = np.abs(a) > 0
                if not np.any(nonzero):
                    r.fail("analog_vals todos cero — señal plana")
                else:
                    corr = float(np.corrcoef(a, d)[0, 1])
                    if corr < 0.999:
                        r.fail(f"Correlación = {corr:.4f} (esperado ≥ 0.999 con FIR identidad)")
        except Exception as e:
            r.fail(f"Excepción: {e}")
        _stop_sim(proc, fd)
        results.append(r)

    # ── Escenario 3: FIR todos-ceros → raw_digital = 0 ───────────────────────
    r = Result("FIR todos-ceros (31 taps): raw_digital = 0 para cualquier entrada")
    proc, fd = _start_sim()
    if proc is None:
        r.fail("simulador no inició"); results.append(r)
    else:
        try:
            _write(fd, cmd_load_fir([0.0] * 31))
            time.sleep(0.05)
            _write(fd, cmd_start())
            pkts = _drain_pkts(fd, 0.5)
            _write(fd, cmd_stop())
            if len(pkts) < MIN_PKTS:
                r.fail(f"Pocos paquetes: {len(pkts)}")
            else:
                max_dig = max(abs(p['raw_digital']) for p in pkts)
                if max_dig != 0:
                    r.fail(f"FIR zeros: max|raw_digital|={max_dig:.6f} (esperado 0)")
        except Exception as e:
            r.fail(f"Excepción: {e}")
        _stop_sim(proc, fd)
        results.append(r)

    # ── Escenario 4: FIR_CLEAR revierte a sintético ───────────────────────────
    r = Result("FIR_CLEAR después de identidad → raw_digital vuelve a sintético (≠ analog)")
    proc, fd = _start_sim()
    if proc is None:
        r.fail("simulador no inició"); results.append(r)
    else:
        try:
            _write(fd, cmd_load_fir([32767.0 / 32768.0]))
            time.sleep(0.05)
            _write(fd, cmd_fir_clear())
            time.sleep(0.05)
            _write(fd, cmd_start())
            pkts = _drain_pkts(fd, 0.5)
            _write(fd, cmd_stop())
            if len(pkts) < MIN_PKTS:
                r.fail(f"Pocos paquetes: {len(pkts)}")
            else:
                a = np.array([p['raw_analog']  for p in pkts], dtype=float)
                d = np.array([p['raw_digital'] for p in pkts], dtype=float)
                diff_rms = float(np.sqrt(np.mean((a - d) ** 2)))
                if diff_rms < 1.0:
                    r.fail(f"Tras FIR_CLEAR, raw_digital ≈ raw_analog (diff={diff_rms:.1f})")
        except Exception as e:
            r.fail(f"Excepción: {e}")
        _stop_sim(proc, fd)
        results.append(r)

    return results


def main():
    print("=" * 68)
    print("  TEST FIR SIMULADOR INTEGRATION — FIR aplicado en paquetes reales")
    print("=" * 68)
    results = run()
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    for r in results: print(r)
    print("─" * 68)
    print(f"  Total: {len(results)}  |  PASS: {passed}  |  FAIL: {failed}")
    print("=" * 68)
    return failed == 0


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
