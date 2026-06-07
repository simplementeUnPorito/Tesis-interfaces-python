"""signal_proc.py — FIR filter, DC removal, and harmonic-notch helpers."""

from __future__ import annotations

import ast
import re
from typing import Optional

import numpy as np
from scipy import signal
from scipy.signal import (
    firls,
    firwin,
    firwin2,
    get_window,
    kaiserord,
    lfilter,
    remez,
    savgol_coeffs,
)


# ── FIR filter ───────────────────────────────────────────────────────────────

_FIR_COMPILE_ERROR = ""


def last_fir_error() -> str:
    """Return the last FIR command parse/evaluation error, if any."""
    return _FIR_COMPILE_ERROR


def _set_fir_error(message: str) -> None:
    global _FIR_COMPILE_ERROR
    _FIR_COMPILE_ERROR = message

def fir_filter(
    b: np.ndarray,
    x: np.ndarray,
    zi: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Apply a linear-phase FIR filter using scipy.signal.lfilter.

    Args:
        b:  1-D coefficient array (numerator).
        x:  Input signal (1-D numpy array).
        zi: Initial filter state (length len(b)-1). Pass None to initialise
            with zeros on first call.

    Returns:
        (y, zi_new) where y is the filtered output and zi_new is the updated
        state for the next call (enables block-by-block processing).
    """
    if zi is None:
        zi = np.zeros(len(b) - 1, dtype=float)
    y, zi_new = lfilter(b, 1.0, x, zi=zi)
    return y, zi_new


def compile_fir_cmd(cmd: str, fs: float) -> Optional[np.ndarray]:
    """
    Compile a FIR filter from a human-readable command string.

    Supported formats (case-insensitive):
        lp <cutoff>            — low-pass, e.g. "lp 200"
        hp <cutoff>            — high-pass, e.g. "hp 10"
        bp <low> <high>        — band-pass, e.g. "bp 10 400"
        bs/sb <low> <high>     — band-stop / notch, e.g. "bs 45 55"
        bs/sb <center>         — band-stop / notch, ±5 Hz around center, e.g. "sb 240"
        numtaps <n> lp <f>     — explicit tap count + low-pass
        firls(..., fs=FS)      — scipy.signal.firls expression
        remez(..., fs=FS)      — scipy.signal.remez expression
        firwin(..., fs=FS)     — scipy.signal.firwin expression
        firwin2(..., fs=FS)    — scipy.signal.firwin2 expression
        b = [...]              — direct coefficient vector

    Returns the coefficient array b, or None on parse failure.
    """
    _set_fir_error("")
    cmd = cmd.strip()
    if not cmd:
        _set_fir_error("empty command")
        return None

    b = _compile_fir_expression(cmd, fs)
    if b is not None:
        return b

    cmd_l = cmd.lower()
    try:
        parts  = cmd_l.split()
        n_taps = 101  # default
        if parts[0] == "numtaps":
            n_taps = int(parts[1])
            parts  = parts[2:]
        ftype = parts[0]
        if ftype in ("lp", "lowpass"):
            cutoff = float(parts[1])
            b = firwin(n_taps, cutoff, fs=fs)
        elif ftype in ("hp", "highpass"):
            cutoff = float(parts[1])
            b = firwin(n_taps, cutoff, pass_zero=False, fs=fs)
        elif ftype in ("bp", "bandpass"):
            low, high = float(parts[1]), float(parts[2])
            b = firwin(n_taps, [low, high], pass_zero=False, fs=fs)
        elif ftype in ("bs", "bandstop", "sb", "stopband", "notch"):
            freqs = [float(p) for p in parts[1:]]
            if len(freqs) == 1:
                half_bw = 5.0  # Hz; banda total = 10 Hz alrededor del centro
                low, high = freqs[0] - half_bw, freqs[0] + half_bw
            elif len(freqs) >= 2:
                low, high = freqs[0], freqs[1]
            else:
                raise ValueError("bs/sb necesita <centro> o <low> <high>")
            b = firwin(n_taps, [low, high], fs=fs)
        else:
            return None
        return _validate_fir_coeffs(b)
    except Exception as exc:
        _set_fir_error(str(exc))
        return None


def _compile_fir_expression(cmd: str, fs: float) -> Optional[np.ndarray]:
    expr = _strip_assignment(cmd)
    if not _looks_like_expression(expr):
        return None

    try:
        tree = ast.parse(expr, mode="eval")
        env = _fir_eval_env(fs)
        _validate_fir_ast(tree, env)
        result = eval(compile(tree, "<fir-cmd>", "eval"), {"__builtins__": {}}, env)
        return _validate_fir_coeffs(result)
    except Exception as exc:
        _set_fir_error(str(exc))
        return None


def _strip_assignment(cmd: str) -> str:
    match = re.match(r"^\s*(?:b|h|coeffs|coef)\s*=\s*(.+)$", cmd, flags=re.IGNORECASE)
    return match.group(1).strip() if match else cmd


def _looks_like_expression(cmd: str) -> bool:
    if any(ch in cmd for ch in "(["):
        return True
    return bool(re.match(r"^\s*(?:b|h|coeffs|coef)\s*=", cmd, flags=re.IGNORECASE))


def _fir_eval_env(fs: float) -> dict[str, object]:
    return {
        "FS": float(fs),
        "fs": float(fs),
        "nyq": float(fs) / 2.0,
        "np": np,
        "signal": signal,
        "array": np.array,
        "arange": np.arange,
        "blackman": np.blackman,
        "concatenate": np.concatenate,
        "firls": firls,
        "firwin": firwin,
        "firwin2": firwin2,
        "get_window": get_window,
        "hamming": np.hamming,
        "hanning": np.hanning,
        "kaiser": np.kaiser,
        "kaiserord": kaiserord,
        "linspace": np.linspace,
        "ones": np.ones,
        "remez": remez,
        "savgol_coeffs": savgol_coeffs,
        "sinc": np.sinc,
        "zeros": np.zeros,
    }


_ALLOWED_FIR_AST_NODES = (
    ast.Expression,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Tuple,
    ast.List,
    ast.keyword,
    ast.UnaryOp,
    ast.UAdd,
    ast.USub,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.Attribute,
)


def _validate_fir_ast(tree: ast.AST, env: dict[str, object]) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_FIR_AST_NODES):
            raise ValueError(f"syntax not allowed: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in env:
            raise ValueError(f"name not allowed: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"attribute not allowed: {node.attr}")
        if isinstance(node, ast.Call) and not isinstance(node.func, (ast.Name, ast.Attribute)):
            raise ValueError("only direct function calls are allowed")


def _validate_fir_coeffs(result: object) -> Optional[np.ndarray]:
    try:
        b = np.asarray(result, dtype=np.float64)
    except Exception as exc:
        _set_fir_error(f"result is not numeric: {exc}")
        return None

    if b.ndim != 1:
        _set_fir_error("result must be a 1-D coefficient vector")
        return None
    if b.size == 0:
        _set_fir_error("empty coefficient vector")
        return None
    if not np.all(np.isfinite(b)):
        _set_fir_error("coefficients must be finite")
        return None
    return b


def harmonic_notch(
    x: np.ndarray,
    fs: float,
    f0: float,
    n_harmonics: int,
) -> np.ndarray:
    """
    Remove f0 and its harmonics from x with a one-shot least-squares fit.

    The interference is modelled as a sum of cosine/sine pairs at
    f0, 2*f0, … n_harmonics*f0. Their amplitudes are fitted to the
    *complete* buffer x by linear least squares (a single np.linalg.lstsq
    solve over the whole capture, not an iterative per-sample update), and
    the fitted sinusoidal model is subtracted from x.

    Mains hum sits at a known, very stable frequency, so once the whole
    capture is available this direct fit is both exact (optimal in the
    least-squares sense) and instantaneous — unlike LMS/NLMS, it needs no
    step size and has no convergence transient or steady-state misadjustment.

    Args:
        x:           Input block (1-D numpy array).
        fs:          Sample rate in Hz.
        f0:          Fundamental frequency to cancel (Hz).
        n_harmonics: Number of harmonics to cancel (including the fundamental).

    Returns:
        x with the fitted f0..n_harmonics*f0 sinusoids subtracted.
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0 or n_harmonics <= 0:
        return x.copy()

    t = np.arange(n, dtype=np.float64)
    cols = []
    for k in range(1, n_harmonics + 1):
        angle = 2.0 * np.pi * (k * f0) / fs * t
        cols.append(np.cos(angle))
        cols.append(np.sin(angle))
    h = np.column_stack(cols)

    coeffs, *_ = np.linalg.lstsq(h, x, rcond=None)
    return x - h @ coeffs


# ── DC removal ───────────────────────────────────────────────────────────────

def dc_remove(buf: np.ndarray) -> np.ndarray:
    """Subtract the mean from buf and return a new zero-mean array."""
    if buf.size == 0:
        return buf
    return buf - buf.mean()
