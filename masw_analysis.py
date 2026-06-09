"""
masw_analysis.py — Análisis MASW desde exportaciones ZIP de Geophone Scope.

Geometría esperada del ensayo:
    Hammer (source) → geo1 (receptor 1) → geo2 (receptor 2) → ...
    Los nodos adicionales (geo3, etc.) se incorporan automáticamente si están
    en el ZIP.

Método de procesamiento:
    Park et al. (1998) phase-shift method — transforma los registros de tiempo
    al dominio frecuencia-velocidad de fase (imagen de dispersión).
    Con sólo 2 receptores también se muestra la estimación directa por
    diferencia de fase entre canales adyacentes.

Uso:
    python masw_analysis.py                              # busca ZIPs en Crudos/
    python masw_analysis.py muestra_20260608_174501.zip  # ZIP específico
    python masw_analysis.py C:/ruta/al/directorio        # carpeta extraída
    python masw_analysis.py --offset1 2 --spacing 2      # geometría en metros

    --offset1   Distancia source→primer receptor [m]  (default: pide interactivo)
    --spacing   Separación entre receptores [m]        (default: pide interactivo)
    --cmin      Velocidad de fase mínima [m/s]          (default: 50)
    --cmax      Velocidad de fase máxima [m/s]          (default: 1000)
    --fmin      Frecuencia mínima de análisis [Hz]      (default: 1)
    --fmax      Frecuencia máxima de análisis [Hz]      (default: Fs/2)
    --no-pick   No mostrar modo de picking de curva
"""

from __future__ import annotations

import argparse
import json
import sys

# Force UTF-8 on Windows consoles (cp1252 can't print some Unicode chars)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
from typing import Optional
from zipfile import ZipFile

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import Normalize
from scipy import signal as scipy_signal

# ── Carga de datos ────────────────────────────────────────────────────────────

def _find_latest_zip(data_dir: Path) -> Optional[Path]:
    zips = sorted(data_dir.glob("*.zip"))
    return zips[-1] if zips else None


def _read_f32le(src, name: str) -> np.ndarray:
    """Lee un array float32-LE desde un ZipFile o directorio."""
    if isinstance(src, ZipFile):
        try:
            return np.frombuffer(src.read(name), dtype="<f4").astype(np.float64)
        except KeyError:
            return np.empty(0)
    else:
        p = Path(src) / name
        if p.exists():
            return np.fromfile(p, dtype="<f4").astype(np.float64)
        return np.empty(0)


def _read_json(src, name: str) -> dict:
    if isinstance(src, ZipFile):
        return json.loads(src.read(name).decode())
    return json.loads((Path(src) / name).read_text(encoding="utf-8"))


def load_capture(path: Path) -> dict:
    """Carga un ZIP o directorio exportado por la web UI.

    Returns dict con:
        fs        : sample rate [Hz]
        channels  : list of dicts {name, raw [V], filt [V], dir}
        metadata  : dict completo del metadata.json
    """
    if path.is_dir():
        src = path
    else:
        src = ZipFile(path)

    try:
        meta = _read_json(src, "metadata.json")
    except (KeyError, FileNotFoundError):
        raise FileNotFoundError(f"No se encontró metadata.json en {path}")

    fs = float(meta.get("fs") or 0)
    channels = []
    for nd in meta.get("nodes", []):
        if not nd.get("raw_file"):
            continue
        raw = _read_f32le(src, nd["raw_file"])
        filt = _read_f32le(src, nd["filt_file"]) if nd.get("filt_file") else np.empty(0)
        channels.append({
            "name": nd.get("name", nd.get("type", f"ch{nd['index']}")),
            "index": nd["index"],
            "raw": raw,
            "filt": filt,
            "dir": nd.get("data_dir", ""),
        })

    if not channels:
        raise ValueError("No se encontraron canales con datos en el archivo.")
    if isinstance(src, ZipFile):
        src.close()

    return {"fs": fs, "channels": channels, "metadata": meta}


# ── MASW — Phase-shift method (Park et al. 1998) ─────────────────────────────

def phase_shift_image(traces: np.ndarray, offsets: np.ndarray,
                      fs: float, c_vec: np.ndarray,
                      fmin: float = 1.0, fmax: Optional[float] = None) -> tuple:
    """Imagen de dispersión por el método de phase-shift.

    Parameters
    ----------
    traces  : (n_receivers, n_samples) — señales temporales de los receptores
    offsets : (n_receivers,) — distancia source→receptor [m]
    fs      : frecuencia de muestreo [Hz]
    c_vec   : (n_c,) — velocidades de fase de prueba [m/s]
    fmin    : frecuencia mínima de análisis [Hz]
    fmax    : frecuencia máxima de análisis [Hz] (default: Nyquist)

    Returns
    -------
    freqs : (n_freqs,) — frecuencias del eje X [Hz]
    image : (n_freqs, n_c) — imagen normalizada de dispersión [0..1]
    """
    n_rec, n_samp = traces.shape
    if fmax is None:
        fmax = fs / 2

    # Hanning window
    win = np.hanning(n_samp)
    traces_w = traces * win[np.newaxis, :]

    # FFT
    NFFT = 2 ** int(np.ceil(np.log2(n_samp)))
    F = np.fft.rfftfreq(NFFT, d=1.0 / fs)
    X = np.fft.rfft(traces_w, n=NFFT, axis=1)

    # Máscara de frecuencias de interés
    freq_mask = (F >= fmin) & (F <= fmax)
    F_sel = F[freq_mask]
    X_sel = X[:, freq_mask]                     # (n_rec, n_freqs)

    # Normalizar por magnitud (quedarse solo con la fase)
    amp = np.abs(X_sel)
    amp[amp < 1e-30] = 1e-30
    P = X_sel / amp                             # (n_rec, n_freqs)

    # Phase-shift summation
    #   S(f, c) = |sum_j exp(-i*2π*f*x_j/c) * P_j(f)| / N
    n_c = len(c_vec)
    n_f = len(F_sel)
    image = np.zeros((n_f, n_c), dtype=np.float64)

    for ci, c in enumerate(c_vec):
        phase_shift = np.exp(-1j * 2 * np.pi * F_sel[np.newaxis, :] * offsets[:, np.newaxis] / c)
        S = np.sum(phase_shift * P, axis=0)     # (n_freqs,)
        image[:, ci] = np.abs(S) / n_rec

    # Normalizar por máximo de cada fila (frecuencia) para destacar el pico
    row_max = image.max(axis=1, keepdims=True)
    row_max[row_max < 1e-30] = 1.0
    image /= row_max

    return F_sel, image


def two_receiver_dispersion(raw1: np.ndarray, raw2: np.ndarray,
                             x1: float, x2: float, fs: float,
                             fmin: float = 1.0, fmax: Optional[float] = None,
                             smooth_hz: float = 5.0) -> tuple:
    """Estimación directa de curva de dispersión con 2 receptores.

    Calcula c(f) = 2π·f·Δx / Δφ(f), donde Δφ es la diferencia de fase
    entre los dos receptores (coherencia cruzada).

    Returns
    -------
    freqs : (n_freqs,) — frecuencias [Hz]
    c_est : (n_freqs,) — velocidad de fase estimada [m/s] (NaN donde incoherente)
    coh   : (n_freqs,) — coherencia entre los dos receptores [0..1]
    """
    if fmax is None:
        fmax = fs / 2
    dx = x2 - x1

    n = len(raw1)
    win = np.hanning(n)
    r1, r2 = raw1 * win, raw2 * win
    NFFT = 2 ** int(np.ceil(np.log2(n)))

    F = np.fft.rfftfreq(NFFT, d=1.0 / fs)
    X1 = np.fft.rfft(r1, n=NFFT)
    X2 = np.fft.rfft(r2, n=NFFT)

    # Coherencia magnética y diferencia de fase
    cross = X2 * np.conj(X1)
    coh = np.abs(cross) / (np.abs(X1) * np.abs(X2) + 1e-30)

    # Suavizado por convolución gaussiana en frecuencia
    df = F[1] - F[0]
    sigma_bins = max(1, int(smooth_hz / df))
    gauss = np.exp(-0.5 * (np.arange(-3 * sigma_bins, 3 * sigma_bins + 1) / sigma_bins) ** 2)
    gauss /= gauss.sum()
    cross_smooth = np.convolve(cross.real, gauss, mode='same') + \
                   1j * np.convolve(cross.imag, gauss, mode='same')

    dphi = np.angle(cross_smooth)   # [-π, π]

    # c = 2π·f·Δx / Δφ
    with np.errstate(divide='ignore', invalid='ignore'):
        c_est = np.where(np.abs(dphi) > 1e-6,
                         2 * np.pi * F * dx / dphi,
                         np.nan)
    c_est[c_est <= 0] = np.nan  # descartar valores negativos / DC

    freq_mask = (F >= fmin) & (F <= fmax)
    return F[freq_mask], c_est[freq_mask], coh[freq_mask]


# ── Gráficas ──────────────────────────────────────────────────────────────────

def plot_waveforms(ax, channels, fs, offsets):
    """Gráfica de registros sísmicos (tiempo vs amplitud, desplazados por offset)."""
    max_amp = max(np.max(np.abs(ch["raw"])) for ch in channels if len(ch["raw"]))
    if max_amp < 1e-12:
        max_amp = 1.0
    t = np.arange(len(channels[0]["raw"])) / fs * 1000   # ms

    scale = offsets[-1] / len(channels) if len(channels) > 1 else 1.0

    for ch, x in zip(channels, offsets):
        raw = ch["raw"]
        if not len(raw):
            continue
        tr = raw / max_amp * scale * 0.8
        ax.plot(t, tr + x, color='royalblue', lw=0.8)
        ax.axhline(y=x, color='gray', lw=0.3, ls='--')

    ax.set_xlabel("Tiempo [ms]")
    ax.set_ylabel("Offset desde source [m]")
    ax.set_title("Registros sísmicos")
    ax.invert_yaxis()
    ax.set_xlim(t[0], t[-1])


def plot_dispersion_image(ax, freqs, c_vec, image, picked=None, title="Imagen de dispersión"):
    im = ax.pcolormesh(freqs, c_vec, image.T, cmap='jet', shading='auto',
                        norm=Normalize(0, 1))
    if picked is not None and len(picked):
        fp, cp = zip(*picked)
        ax.plot(fp, cp, 'w--', lw=1.5, label='Curva seleccionada')
        ax.legend(fontsize=8)
    ax.set_xlabel("Frecuencia [Hz]")
    ax.set_ylabel("Velocidad de fase [m/s]")
    ax.set_title(title)
    return im


def plot_two_receiver(ax, freqs, c_est, coh, cmin, cmax):
    mask = (c_est >= cmin) & (c_est <= cmax) & (coh > 0.5)
    ax.scatter(freqs[mask], c_est[mask], c=coh[mask], cmap='plasma',
               s=8, vmin=0.5, vmax=1.0, label='coh > 0.5')
    ax.scatter(freqs[~mask], c_est[~mask], color='lightgray', s=4, alpha=0.3, label='coh ≤ 0.5')
    ax.set_ylim(cmin, cmax)
    ax.set_xlabel("Frecuencia [Hz]")
    ax.set_ylabel("Velocidad de fase [m/s]")
    ax.set_title("Dispersión 2-receptores (diferencia de fase)")
    ax.legend(fontsize=8)


# ── Picking interactivo ───────────────────────────────────────────────────────

class DispersionPicker:
    def __init__(self, ax, freqs, c_vec, image):
        self.ax = ax
        self.freqs = freqs
        self.c_vec = c_vec
        self.image = image
        self.picked = []
        self._line, = ax.plot([], [], 'w--o', lw=1.5, ms=5, label='Curva elegida')
        ax.legend(fontsize=8)
        self._cid = ax.figure.canvas.mpl_connect('button_press_event', self._on_click)
        print("\n[Picking] Click izquierdo en la imagen de dispersión para marcar puntos.")
        print("[Picking] Click derecho para deshacer el último punto.")
        print("[Picking] Cerrar la ventana para terminar.\n")

    def _on_click(self, event):
        if event.inaxes is not self.ax:
            return
        f, c = event.xdata, event.ydata
        if f is None or c is None:
            return
        if event.button == 1:
            self.picked.append((f, c))
            print(f"  → f={f:.1f} Hz  c={c:.1f} m/s")
        elif event.button == 3 and self.picked:
            self.picked.pop()
            print("  ← deshacer")
        self.picked.sort(key=lambda p: p[0])
        if self.picked:
            fp, cp = zip(*self.picked)
            self._line.set_data(fp, cp)
        else:
            self._line.set_data([], [])
        self.ax.figure.canvas.draw_idle()

    def get_curve(self):
        return list(self.picked)


# ── CLI / main ────────────────────────────────────────────────────────────────

def _ask_geometry(n_channels: int) -> tuple[float, float]:
    print("\n─── Geometría del ensayo ─────────────────────────────────────────")
    print(f"  Source (Hammer) en x = 0 m")
    print(f"  Receptores: {n_channels} canales (geo1..geo{n_channels})")
    try:
        x1 = float(input("  Distancia source → receptor 1 (geo1) [m]: "))
        dx = float(input("  Separación entre receptores [m]: "))
    except (ValueError, EOFError):
        print("  Usando defaults: offset1=2 m, spacing=2 m")
        x1, dx = 2.0, 2.0
    return x1, dx


def main():
    repo_root = Path(__file__).resolve().parent.parent.parent
    crudos_dir = repo_root / "Crudos"

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", help="ZIP, directorio extraído, o directorio con ZIPs")
    parser.add_argument("--offset1",  type=float, default=None, help="Distancia source→geo1 [m]")
    parser.add_argument("--spacing",  type=float, default=None, help="Separación entre receptores [m]")
    parser.add_argument("--cmin",     type=float, default=50.0, help="Velocidad de fase mínima [m/s]")
    parser.add_argument("--cmax",     type=float, default=1000.0, help="Velocidad de fase máxima [m/s]")
    parser.add_argument("--fmin",     type=float, default=1.0, help="Frecuencia mínima [Hz]")
    parser.add_argument("--fmax",     type=float, default=None, help="Frecuencia máxima [Hz]")
    parser.add_argument("--no-pick",  action="store_true", help="Sin modo picking interactivo")
    parser.add_argument("--nc",       type=int, default=200, help="Puntos en el eje de velocidad")
    args = parser.parse_args()

    # ── Localizar el archivo/directorio de entrada ────────────────────────────
    if args.input:
        path = Path(args.input)
    elif crudos_dir.exists():
        # Buscar el ZIP o carpeta más reciente en Crudos/
        candidates = sorted(crudos_dir.glob("*.zip")) + \
                     [d for d in sorted(crudos_dir.iterdir()) if d.is_dir() and (d / "metadata.json").exists()]
        if not candidates:
            sys.exit(f"No se encontraron capturas en {crudos_dir}")
        path = candidates[-1]
        print(f"Usando captura más reciente: {path.name}")
    else:
        sys.exit("Proporciona un archivo ZIP o directorio como argumento.")

    if not path.exists():
        sys.exit(f"No existe: {path}")

    # ── Cargar datos ──────────────────────────────────────────────────────────
    cap = load_capture(path)
    fs = cap["fs"]
    channels = cap["channels"]
    n_ch = len(channels)

    print(f"\nCaptura cargada: fs={fs:.0f} Hz, {n_ch} canales, "
          f"{len(channels[0]['raw'])} muestras ({len(channels[0]['raw'])/fs*1000:.1f} ms)")
    for ch in channels:
        print(f"  {ch['name']:12s} — raw: {len(ch['raw'])} muestras")

    # ── Geometría ─────────────────────────────────────────────────────────────
    if args.offset1 is not None and args.spacing is not None:
        x1, dx = args.offset1, args.spacing
    else:
        x1, dx = _ask_geometry(n_ch)

    offsets = np.array([x1 + i * dx for i in range(n_ch)])
    print(f"\nGeometría: source @ 0 m, receptores @ {offsets} m")

    # ── Armar matriz de trazas ─────────────────────────────────────────────────
    min_samp = min(len(ch["raw"]) for ch in channels)
    traces = np.stack([ch["raw"][:min_samp] for ch in channels])   # (n_ch, n_samp)

    fmax_nyq = fs / 2
    fmax_use = min(args.fmax or fmax_nyq, fmax_nyq)

    # ── Imagen de dispersión (phase-shift method) ──────────────────────────────
    c_vec = np.linspace(args.cmin, args.cmax, args.nc)
    print(f"\nComputando imagen de dispersión ({args.nc} velocidades, "
          f"{args.fmin:.0f}–{fmax_use:.0f} Hz)…")
    freqs, image = phase_shift_image(traces, offsets, fs, c_vec,
                                     fmin=args.fmin, fmax=fmax_use)
    print("Listo.")

    # ── Dispersión de 2 receptores (si hay al menos 2) ─────────────────────────
    two_rx = None
    if n_ch >= 2:
        raw1 = channels[0]["raw"][:min_samp]
        raw2 = channels[1]["raw"][:min_samp]
        fr2, c2, coh2 = two_receiver_dispersion(raw1, raw2, offsets[0], offsets[1],
                                                 fs, fmin=args.fmin, fmax=fmax_use)
        two_rx = (fr2, c2, coh2)

    # ── Graficar ──────────────────────────────────────────────────────────────
    n_plots = 3 if two_rx else 2
    fig = plt.figure(figsize=(5 * n_plots, 6))
    fig.suptitle(f"MASW — {path.name}  |  fs={fs:.0f} Hz  |  "
                 f"geom: x1={x1}m, Δx={dx}m", fontsize=11)
    gs = gridspec.GridSpec(1, n_plots, figure=fig, wspace=0.35)

    # Col 0: registros
    ax0 = fig.add_subplot(gs[0])
    plot_waveforms(ax0, channels, fs, offsets)

    # Col 1: imagen de dispersión
    ax1 = fig.add_subplot(gs[1])
    im = plot_dispersion_image(ax1, freqs, c_vec, image,
                               title=f"Phase-shift (N={n_ch})")
    plt.colorbar(im, ax=ax1, label='Amplitud normalizada')

    picker = None
    if not args.no_pick:
        picker = DispersionPicker(ax1, freqs, c_vec, image)

    # Col 2: dispersión 2-receptores (si aplica)
    if two_rx:
        ax2 = fig.add_subplot(gs[2])
        plot_two_receiver(ax2, *two_rx, args.cmin, args.cmax)

    plt.tight_layout()

    if n_ch < 6:
        print(f"\nNOTA: con solo {n_ch} receptor(es) la imagen de dispersion tiene "
              "resolucion limitada. Para MASW se recomiendan >= 6 receptores.")

    plt.show()

    if picker and picker.get_curve():
        picked = picker.get_curve()
        print(f"\nCurva de dispersión seleccionada ({len(picked)} puntos):")
        print("  f [Hz]     c [m/s]")
        for fp, cp in picked:
            print(f"  {fp:8.2f}   {cp:8.1f}")
        # Guardar
        out_path = path.with_suffix(".dispersion.txt") if path.suffix == ".zip" else path / "dispersion.txt"
        np.savetxt(out_path, picked, fmt="%.4f", header="freq_hz  c_ms", comments="# ")
        print(f"\nGuardado en: {out_path}")


if __name__ == "__main__":
    main()
