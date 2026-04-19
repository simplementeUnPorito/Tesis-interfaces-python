"""
vdac_generator.py — VDAC test signal generators.

All generators produce sequences of uint8 values (0..255) centered on 128
(DC midpoint for differential operation). The PSoC computes VDAC_n = 256 - VDAC_p.

Typical use: generate a 1-second seismic pulse at 3000-point resolution,
load it into the PSoC via cmd_vdac_load().
"""

import numpy as np


def _to_vdac(signal_normalized: np.ndarray, amplitude: float = 0.9) -> list[int]:
    """
    Convert a normalized signal (-1..1) to VDAC uint8 values (0..255) centered on 128.
    amplitude: fraction of half-scale to use (default 0.9 = 115 counts swing).
    """
    clipped = np.clip(signal_normalized * amplitude, -1.0, 1.0)
    values = np.round(128.0 + clipped * 127.0).astype(np.uint8)
    return values.tolist()


def ricker(sigma_s: float = 0.1, fs: float = 3000.0,
           amplitude: float = 0.9) -> list[int]:
    """
    Ricker wavelet (Mexican hat / negative of 2nd derivative of Gaussian).
    Common synthetic seismic source signature.

    sigma_s: standard deviation of the Gaussian envelope (seconds)
    fs:      VDAC update rate (Hz), default 3000
    """
    sigma_s = max(sigma_s, 1.0 / fs)   # guard: at least one sample period
    t = np.arange(0, 1.0, 1.0 / fs)
    t = t - t[-1] / 2.0        # Center around zero
    u = t / sigma_s
    y = (1.0 - u**2) * np.exp(-0.5 * u**2)
    y /= np.max(np.abs(y))
    return _to_vdac(y, amplitude)


def gauss3(sigma_s: float = 0.1, fs: float = 3000.0,
           amplitude: float = 0.9) -> list[int]:
    """
    Third derivative of a Gaussian (Berlage-style seismic pulse).
    sigma_s: standard deviation in seconds.
    """
    sigma_s = max(sigma_s, 1.0 / fs)   # guard: at least one sample period
    t = np.arange(0, 1.0, 1.0 / fs)
    t = t - t[-1] / 2.0
    u = t / sigma_s
    y = (u**3 - 3.0 * u) * np.exp(-0.5 * u**2)
    y /= np.max(np.abs(y))
    return _to_vdac(y, amplitude)


def sine(freq_hz: float, duration_s: float = 1.0, fs: float = 3000.0,
         amplitude: float = 0.9) -> list[int]:
    """Simple sine wave."""
    t = np.arange(0, duration_s, 1.0 / fs)
    y = np.sin(2.0 * np.pi * freq_hz * t)
    return _to_vdac(y, amplitude)


def chirp(f0: float = 10.0, f1: float = 240.0, duration_s: float = 1.0,
          fs: float = 3000.0, amplitude: float = 0.9) -> list[int]:
    """Linear frequency sweep (chirp) from f0 to f1 Hz."""
    from scipy.signal import chirp as sp_chirp
    t = np.arange(0, duration_s, 1.0 / fs)
    y = sp_chirp(t, f0=f0, f1=f1, t1=duration_s, method='linear')
    return _to_vdac(y, amplitude)


def impulse(fs: float = 3000.0, amplitude: float = 0.9) -> list[int]:
    """Single-sample impulse at the center of a 1-second window."""
    n = int(fs)
    y = np.zeros(n)
    y[n // 2] = 1.0
    return _to_vdac(y, amplitude)


def silence(fs: float = 3000.0) -> list[int]:
    """All zeros (DC midpoint = 128). Use to park VDACs at rest."""
    return [128] * int(fs)


def from_array(values: np.ndarray, amplitude: float = 0.9) -> list[int]:
    """
    Convert any arbitrary normalized numpy array to VDAC sequence.
    Input is normalized internally to +/-1 before scaling.
    """
    peak = np.max(np.abs(values))
    if peak > 0:
        values = values / peak
    return _to_vdac(values, amplitude)


def from_csv(filepath: str, amplitude: float = 0.9) -> list[int]:
    """
    Load a VDAC sequence from a single-column CSV file.
    Values are expected as raw floats; they are normalized automatically.
    """
    data = np.loadtxt(filepath, delimiter=',')
    if data.ndim > 1:
        data = data[:, 0]
    return from_array(data, amplitude)
