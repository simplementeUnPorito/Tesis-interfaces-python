"""
digital_filters.py — Real-time FIR and IIR filter engine.

Supports per-channel virtual filtering applied to the live data stream.
Uses scipy.signal.sosfilt with persistent state (zi) for causal real-time
processing with no latency artifacts at block boundaries.

Usage:
    filt = ChannelFilter()
    filt.set_fir(coeffs)          # load FIR h[n] coefficients
    filt.set_iir_ba(b, a)         # load IIR from b/a coefficients
    filt.set_iir_sos(sos)         # load IIR from SOS matrix (preferred)
    filt.set_notch(freq, Q, fs)   # design and load a notch filter
    filt.set_bandpass(f1, f2, fs, order)
    filt.reset()                  # clear state

    y = filt.process(x)           # x: np.ndarray, returns filtered np.ndarray
"""

import numpy as np
from scipy import signal


class ChannelFilter:
    """Real-time causal filter for one data channel."""

    def __init__(self):
        self._sos   = None     # SOS matrix (IIR path, preferred)
        self._b     = None     # FIR coefficients
        self._zi    = None     # Filter state
        self._mode  = 'none'   # 'none', 'fir', 'iir'
        self._fresh = True     # True = zi has never been run (may scale by x[0])

    # ── Filter design / loading ───────────────────────────────────────────────

    def set_fir(self, coeffs):
        """Load FIR filter from h[n] coefficient array."""
        self._b     = np.asarray(coeffs, dtype=np.float64)
        # Convert to SOS for uniform processing path
        self._sos   = signal.tf2sos(self._b, [1.0])
        self._zi    = signal.sosfilt_zi(self._sos)
        self._mode  = 'fir'
        self._fresh = True

    def set_iir_ba(self, b, a):
        """Load IIR filter from b/a coefficient arrays (converts to SOS internally)."""
        self._sos   = signal.tf2sos(b, a)
        self._zi    = signal.sosfilt_zi(self._sos)
        self._mode  = 'iir'
        self._fresh = True

    def set_iir_sos(self, sos):
        """Load IIR filter from second-order-sections matrix (most numerically stable)."""
        self._sos   = np.asarray(sos, dtype=np.float64)
        self._zi    = signal.sosfilt_zi(self._sos)
        self._mode  = 'iir'
        self._fresh = True

    def set_notch(self, freq_hz: float, Q: float, fs: float):
        """Design and load a notch (band-stop) filter."""
        b, a = signal.iirnotch(freq_hz, Q, fs)
        self.set_iir_ba(b, a)

    def set_bandpass(self, f_low: float, f_high: float, fs: float, order: int = 4):
        """Design and load a Butterworth bandpass filter."""
        sos = signal.butter(order, [f_low, f_high], btype='bandpass',
                            fs=fs, output='sos')
        self.set_iir_sos(sos)

    def set_highpass(self, f_cutoff: float, fs: float, order: int = 4):
        """Design and load a Butterworth highpass filter."""
        sos = signal.butter(order, f_cutoff, btype='highpass', fs=fs, output='sos')
        self.set_iir_sos(sos)

    def set_lowpass(self, f_cutoff: float, fs: float, order: int = 4):
        """Design and load a Butterworth lowpass filter."""
        sos = signal.butter(order, f_cutoff, btype='lowpass', fs=fs, output='sos')
        self.set_iir_sos(sos)

    def reset(self):
        """Clear filter state (use after loading new coefficients or on reconnect)."""
        if self._sos is not None:
            self._zi    = signal.sosfilt_zi(self._sos)
            self._fresh = True

    def clear(self):
        """Remove the filter entirely (pass-through mode)."""
        self._sos   = None
        self._b     = None
        self._zi    = None
        self._mode  = 'none'
        self._fresh = True

    # ── Real-time processing ──────────────────────────────────────────────────

    def process(self, x: np.ndarray) -> np.ndarray:
        """
        Apply the filter to a new block of samples.
        State (zi) is preserved across calls for continuous processing.
        Returns the filtered array, or a copy of x if no filter is loaded.
        """
        if self._sos is None or len(x) == 0:
            return x.copy()

        if self._zi is None:
            self._zi    = signal.sosfilt_zi(self._sos)
            self._fresh = True

        # Scale zi by x[0] only on the first call to reduce the startup transient.
        # On subsequent calls zi is the live filter state and must not be scaled.
        if self._fresh:
            zi_in       = self._zi * x[0]
            self._fresh = False
        else:
            zi_in = self._zi

        y, self._zi = signal.sosfilt(self._sos, x, zi=zi_in)
        return y

    @property
    def active(self) -> bool:
        return self._mode != 'none'

    @property
    def mode(self) -> str:
        return self._mode


# ── Convenience factory functions ─────────────────────────────────────────────

def make_notch(freq_hz: float, Q: float, fs: float) -> ChannelFilter:
    f = ChannelFilter()
    f.set_notch(freq_hz, Q, fs)
    return f

def make_bandpass(f_low: float, f_high: float, fs: float,
                  order: int = 4) -> ChannelFilter:
    f = ChannelFilter()
    f.set_bandpass(f_low, f_high, fs, order)
    return f

def make_highpass(f_cutoff: float, fs: float, order: int = 4) -> ChannelFilter:
    f = ChannelFilter()
    f.set_highpass(f_cutoff, fs, order)
    return f


# ── PSoC FIR firmware helpers ─────────────────────────────────────────────────

def design_fir_lowpass(f_cutoff: float, fs: float, n_taps: int = 63) -> np.ndarray:
    """Design a windowed FIR lowpass (Kaiser window) — returns float64 coefficients."""
    from scipy.signal import firwin
    return firwin(n_taps, f_cutoff, window='hamming', fs=fs)


def design_fir_notch(f_notch: float, fs: float, n_taps: int = 255) -> np.ndarray:
    """Design a FIR notch filter centered at f_notch.

    Windowed FIR transition bandwidth ≈ 8·fs/N.  For meaningful stop-band
    attenuation the stop-band width must exceed that minimum.  We use
    bw = max(20.0, f_notch * 0.33) to ensure ≥ 20 dB with N ≥ 127.
    Default n_taps=255 (PSoC firmware maximum).
    """
    from scipy.signal import firwin
    # Stop-band width must comfortably exceed the filter's resolution
    bw = max(20.0, f_notch * 0.33)
    f_lo = max(1.0, f_notch - bw / 2)
    f_hi = min(fs / 2 - 1.0, f_notch + bw / 2)
    return firwin(n_taps, [f_lo, f_hi], window='hamming', pass_zero=True, fs=fs)


def coeffs_to_q15(coeffs_float) -> list[int]:
    """Convert float coefficients in [-1,1] to Q1.15 int16 list.
    Scale factor is 2^15 = 32768: -1.0 → -32768 (exact), +1.0 → 32767 (saturated)."""
    return [max(-32768, min(32767, int(round(c * 32768.0))))
            for c in coeffs_float]
