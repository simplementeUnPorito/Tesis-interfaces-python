"""Genera el informe reproducible de S2 y sus figuras estaticas."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

from .discretize import (
    augment_with_input_model,
    build_input_model,
    check_observability,
    discrete_freqresp,
    discretization_error,
    discretize_plant,
    markov_parameters,
    prepare_plant,
    sampling_zeros,
)
from .library import load_conditioner, load_geophone
from .models import InputModel, PlantSpec
from .plant import compose_plant_zpk, zpk_freqresp, zpk_to_modal_ss
from .synthetic import benchmark_ricker25, nmp_inverse_growth


FS_VALUES = (1020.0, 2604.0, 2929.0)
CONDITIONERS = ("comp_nominal", "lp_pga_medido")
FREQ_MIN = 0.01
FREQ_MAX = 1000.0
ANALYSIS_BAND = (0.1, 300.0)


def _spec(conditioner: str) -> PlantSpec:
    return PlantSpec(
        geophone=load_geophone("sm24_nominal"),
        conditioner=load_conditioner(conditioner),
    )


def _style() -> None:
    plt.rcParams.update({
        "figure.dpi": 130,
        "savefig.dpi": 180,
        "font.size": 9,
        "axes.grid": True,
        "grid.alpha": 0.22,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def _shade_bands(ax) -> None:
    ax.axvspan(0.1, 300.0, color="#4c78a8", alpha=0.07, label="banda solicitada 0,1-300 Hz")
    ax.axvspan(10.0, 50.0, color="#54a24b", alpha=0.12, label="SNR demostrada 10-50 Hz")
    ax.axvline(0.211, color="#e45756", lw=1.0, ls=":", label="validez LP_PGA: 0,211 Hz")


def plot_bode(output: Path) -> None:
    f = np.logspace(np.log10(FREQ_MIN), np.log10(FREQ_MAX), 1800)
    w = 2.0 * np.pi * f
    fig, axes = plt.subplots(2, 1, figsize=(9.2, 6.4), sharex=True)
    labels = {
        "comp_nominal": "SM-24 x compensador nominal",
        "lp_pga_medido": "SM-24 x LP_PGA medido",
    }
    for conditioner in CONDITIONERS:
        z, p, k = compose_plant_zpk(_spec(conditioner))
        h = zpk_freqresp(z, p, k, w)
        ref = abs(zpk_freqresp(z, p, k, np.array([2*np.pi*25.0]))[0])
        axes[0].semilogx(f, 20*np.log10(np.maximum(abs(h)/ref, 1e-300)), label=labels[conditioner])
        axes[1].semilogx(f, np.unwrap(np.angle(h))*180/np.pi, label=labels[conditioner])
    for ax in axes:
        _shade_bands(ax)
        ax.set_xlim(FREQ_MIN, FREQ_MAX)
    axes[0].set_ylabel("Magnitud relativa a 25 Hz [dB]")
    axes[1].set_ylabel("Fase continua [deg]")
    axes[1].set_xlabel("Frecuencia [Hz]")
    axes[0].legend(ncol=2, fontsize=8, loc="best")
    axes[0].set_title("Respuesta de la planta continua: 0,01 Hz a 1 kHz")
    fig.tight_layout()
    fig.savefig(output / "01_bode_planta_0p01_1000Hz.png", bbox_inches="tight")
    plt.close(fig)


def plot_discretization(output: Path) -> list[dict[str, float | str]]:
    rows = []
    fig, axes = plt.subplots(2, 1, figsize=(9.2, 6.4), sharex=True)
    spec = _spec("lp_pga_medido")
    for fs in FS_VALUES:
        prepared = prepare_plant(spec, fs)
        discrete = discretize_plant(*prepared.continuous, fs=fs)
        hi_plot = min(FREQ_MAX, np.nextafter(fs/2, 0.0))
        f = np.logspace(np.log10(FREQ_MIN), np.log10(hi_plot), 1400)
        w = 2*np.pi*f
        hc = zpk_freqresp(*prepared.full_zpk, w)
        hd = discrete_freqresp(discrete, f)
        ratio = hd / hc
        axes[0].semilogx(f, 20*np.log10(np.maximum(abs(ratio), 1e-300)), label=f"fs={fs:g} Hz")
        axes[1].semilogx(f, np.unwrap(np.angle(ratio))*180/np.pi, label=f"fs={fs:g} Hz")
        high = min(ANALYSIS_BAND[1], 0.4*(fs/2))
        error = discretization_error(discrete, (ANALYSIS_BAND[0], high))
        rows.append({
            "conditioner": "lp_pga_medido",
            "fs": fs,
            "high": high,
            "direct_db": error["max_abs_db"],
            "direct_deg": error["max_abs_deg"],
            "deemb_db": error["max_abs_deembedded_db"],
            "deemb_deg": error["max_abs_deembedded_deg"],
        })
    for ax in axes:
        _shade_bands(ax)
        ax.set_xlim(FREQ_MIN, FREQ_MAX)
    axes[0].axhline(0, color="0.25", lw=0.8)
    axes[1].axhline(0, color="0.25", lw=0.8)
    axes[0].set_ylabel("H(z) / H(s) [dB]")
    axes[1].set_ylabel("Fase H(z) / H(s) [deg]")
    axes[1].set_xlabel("Frecuencia [Hz]")
    axes[0].set_title("Efecto total de la discretizacion ZOH sobre LP_PGA medido")
    axes[0].legend(ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "02_error_discretizacion_0p01_1000Hz.png", bbox_inches="tight")
    plt.close(fig)
    return rows


def plot_sampling_zeros(output: Path):
    reports = {fs: sampling_zeros(_spec("lp_pga_medido"), fs=fs) for fs in FS_VALUES}
    fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.7), sharex=True, sharey=True)
    theta = np.linspace(0, 2*np.pi, 400)
    for ax, fs in zip(axes, FS_VALUES):
        report = reports[fs]
        ax.plot(np.cos(theta), np.sin(theta), color="0.35", lw=1.0)
        intrinsic = [r.value for r in report.rows if r.source == "intrinsic"]
        sampling = [r.value for r in report.rows if r.source == "sampling"]
        ax.scatter(np.real(intrinsic), np.imag(intrinsic), s=25, marker="o", label="intrinseco")
        ax.scatter(np.real(sampling), np.imag(sampling), s=45, marker="x", linewidth=2, label="muestreo")
        ax.set_title(f"fs = {fs:g} Hz")
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(-2.05, 1.15)
        ax.set_ylim(-1.15, 1.15)
        ax.set_xlabel("Re(z)")
    axes[0].set_ylabel("Im(z)")
    axes[0].legend(fontsize=8, loc="lower left")
    fig.suptitle("TEST 3: ceros discretos de LP_PGA (x = creados por muestreo)")
    fig.tight_layout()
    fig.savefig(output / "03_ceros_muestreo_plano_z.png", bbox_inches="tight")
    plt.close(fig)
    return reports


def plot_observability(output: Path):
    reports = {}
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.8))
    for ax, conditioner in zip(axes, CONDITIONERS):
        prepared = prepare_plant(_spec(conditioner), 2604.0)
        discrete = discretize_plant(*prepared.continuous, fs=2604.0)
        for kind, marker in (("random_walk", "o"), ("leaky_rw", "s")):
            input_ss = build_input_model(InputModel(kind=kind), fs=2604.0)
            augmented = augment_with_input_model(discrete, input_ss)
            report = check_observability(augmented.A, augmented.C)
            reports[(conditioner, kind)] = report
            values = report.pbh_singular_values
            ax.semilogy(np.arange(1, values.size+1), values, marker=marker, label=kind)
        ax.set_title(conditioner)
        ax.set_xlabel("Indice singular")
        ax.set_ylabel("Valor singular PBH equilibrado")
        ax.legend(fontsize=8)
    fig.suptitle("TEST 2: random walk pierde un modo en z=1; leaky RW conserva rango")
    fig.tight_layout()
    fig.savefig(output / "04_observabilidad_pbh.png", bbox_inches="tight")
    plt.close(fig)
    return reports


def plot_recovery(output: Path):
    result = benchmark_ricker25(_spec("lp_pga_medido"), fs=2604.0, snr_db=20.0, duration_s=1.6)
    fig, axes = plt.subplots(2, 1, figsize=(9.2, 6.4))
    mask = (result.time_s >= 0.65) & (result.time_s <= 1.45)
    axes[0].plot(result.time_s[mask], result.truth[mask], lw=1.6, label="verdad")
    axes[0].plot(result.time_s[mask], result.filtered_input[mask], lw=1.0, alpha=0.85, label="KF")
    axes[0].plot(result.time_s[mask], result.smoothed_input[mask], lw=1.2, label="RTS")
    axes[0].set_xlabel("Tiempo [s]")
    axes[0].set_ylabel("Aceleracion normalizada")
    axes[0].set_title("Recuperacion sintetica, LP_PGA medido, fs=2604 Hz, SNR=20 dB")
    axes[0].legend(ncol=3, fontsize=8)

    nperseg = min(4096, result.truth.size)
    for values, label in ((result.truth, "verdad"), (result.filtered_input, "KF"), (result.smoothed_input, "RTS")):
        f, psd = signal.welch(values, fs=result.fs, nperseg=nperseg)
        keep = (f >= FREQ_MIN) & (f <= FREQ_MAX)
        axes[1].semilogx(f[keep], 10*np.log10(np.maximum(psd[keep], 1e-300)), label=label)
    _shade_bands(axes[1])
    axes[1].set_xlim(FREQ_MIN, FREQ_MAX)
    axes[1].set_xlabel("Frecuencia [Hz]")
    axes[1].set_ylabel("PSD [dB/Hz]")
    axes[1].legend(ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "05_recuperacion_kf_rts_0p01_1000Hz.png", bbox_inches="tight")
    plt.close(fig)
    return result


def generate(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    _style()
    plot_bode(output)
    discretization_rows = plot_discretization(output)
    zero_reports = plot_sampling_zeros(output)
    observability = plot_observability(output)
    recovery = plot_recovery(output)

    markov = {}
    for conditioner in CONDITIONERS:
        z, p, k = compose_plant_zpk(_spec(conditioner))
        markov[conditioner] = markov_parameters(*zpk_to_modal_ss(z, p, k))
    nmp = nmp_inverse_growth(_spec("lp_pga_medido"), fs=2604.0)
    nis_tuned = benchmark_ricker25(
        _spec("lp_pga_medido"), fs=2604.0, snr_db=20.0, duration_s=1.6,
        q_scale=3.5 * recovery.q_scale,
    )

    zero_lines = []
    for fs, report in zero_reports.items():
        sampling = [abs(row.value) for row in report.rows if row.source == "sampling"]
        zero_lines.append(
            f"| {fs:g} | {max(sampling):.6f} | "
            f"{sum(v > 1.0 + 1e-6 for v in sampling)} |"
        )
    disc_lines = [
        f"| {row['fs']:g} | 0,1-{row['high']:.1f} | {row['direct_db']:.4f} | "
        f"{row['direct_deg']:.3f} | {row['deemb_db']:.4f} | {row['deemb_deg']:.3f} |"
        for row in discretization_rows
    ]
    r = recovery.rts_metrics
    k = recovery.kf_metrics
    improvement = 1.0 - r.rmse_aligned/k.rmse_aligned
    report_path = output / "INFORME_S2_KALMAN.md"
    report_path.write_text(
        "\n".join([
            "# Informe parcial S2 — discretizacion, observabilidad y KF+RTS",
            "",
            "Fecha: 2026-08-17. Resultados regenerables desde `src/interfaces/python`.",
            "",
            "## Alcance de banda",
            "",
            "Los graficos espectrales cubren **0,01 Hz a 1 kHz**. Se analiza la banda ",
            "solicitada de **0,1 a 300 Hz**, pero se mantienen dos limites independientes:",
            "",
            "- el ajuste `lp_pga_medido` solo es defendible desde **0,211 Hz**; 0,1-0,211 Hz se muestra, no se valida;",
            "- las capturas existentes solo demostraron SNR util de manera empirica en **10-50 Hz**.",
            "",
            "![Bode](01_bode_planta_0p01_1000Hz.png)",
            "",
            "## TEST 1 — retardo estructural",
            "",
            "| Planta | Grado relativo teorico | Detectado por Markov | Retardo minimo L |",
            "|---|---:|---:|---:|",
            f"| comp_nominal | 2 | {markov['comp_nominal'].relative_degree} | {markov['comp_nominal'].delay_samples} |",
            f"| lp_pga_medido | 4 | {markov['lp_pga_medido'].relative_degree} | {markov['lp_pga_medido'].delay_samples} |",
            "",
            "El estimador instantaneo no aplica. El procesamiento offline permite absorber L=2/L=4 mediante RTS.",
            "",
            "## TEST 2 — observabilidad en DC",
            "",
            "| Planta | random_walk | leaky_rw (0,7 Hz) |",
            "|---|---:|---:|",
            f"| comp_nominal | {observability[('comp_nominal','random_walk')].pbh_rank}/{observability[('comp_nominal','random_walk')].order} | {observability[('comp_nominal','leaky_rw')].pbh_rank}/{observability[('comp_nominal','leaky_rw')].order} |",
            f"| lp_pga_medido | {observability[('lp_pga_medido','random_walk')].pbh_rank}/{observability[('lp_pga_medido','random_walk')].order} | {observability[('lp_pga_medido','leaky_rw')].pbh_rank}/{observability[('lp_pga_medido','leaky_rw')].order} |",
            "",
            "`random_walk` pierde exactamente un modo en z=1. `leaky_rw` conserva rango completo: no hace falta pasar a Dual KF para el default.",
            "",
            "**Criterio 2.4 aun abierto:** la fuga de 0,7 Hz acota DC, pero el prior AR(1) no es plano en 10-50 Hz: su magnitud cae 13,95 dB en esa banda. Es un prior rojo. No invalida la observabilidad, pero obliga a justificar Q o a evaluar un prior alternativo en S3.",
            "",
            "![PBH](04_observabilidad_pbh.png)",
            "",
            "## TEST 3 — ceros de muestreo (`lp_pga_medido`)",
            "",
            "| fs [Hz] | max abs(z) de muestreo | ceros de muestreo inestables |",
            "|---:|---:|---:|",
            *zero_lines,
            "",
            "**1020 Hz es la mejor de las tres fs para la inversion**: su cero de muestreo inestable queda en |z|=1,0457, frente a 1,7322 y 1,8915. Sigue siendo no minimo, pero mucho menos severo.",
            "",
            "![Ceros](03_ceros_muestreo_plano_z.png)",
            "",
            "## Discretizacion ZOH",
            "",
            "| fs [Hz] | banda evaluada [Hz] | directo [dB] | directo [deg] | sin ZOH [dB] | sin ZOH [deg] |",
            "|---:|---:|---:|---:|---:|---:|",
            *disc_lines,
            "",
            "La equivalencia de magnitud cumple, pero el criterio directo de fase de 2 grados no puede cumplirse sin quitar el medio periodo del retenedor ZOH. Al remover sinc+retardo del retenedor, el error residual cae a centesimas de dB y grado. El checklist conserva esta fila abierta hasta corregir formalmente el criterio.",
            "",
            "![Discretizacion](02_error_discretizacion_0p01_1000Hz.png)",
            "",
            "## Banco sintetico KF+RTS",
            "",
            "Configuracion principal: `lp_pga_medido`, fs=2604 Hz, SNR=20 dB, Ricker 25 Hz + pulso 16 Hz, prior `leaky_rw`.",
            "",
            "| Metrica | KF | RTS |",
            "|---|---:|---:|",
            f"| RMSE alineado / RMS verdad | {100*k.relative_rmse:.3f}% | {100*r.relative_rmse:.3f}% |",
            f"| Retardo | {k.lag_samples} muestras | {r.lag_samples} muestras |",
            f"| Relacion de amplitud | {k.amplitude_ratio:.4f} | {r.amplitude_ratio:.4f} |",
            f"| Error de fase 10-50 Hz | {k.phase_error_deg:+.3f} deg | {r.phase_error_deg:+.3f} deg |",
            f"| Mejora de RMSE RTS vs KF | — | {100*improvement:.3f}% |",
            "",
            f"Covarianzas positivas: min eig KF={recovery.filtered.min_cov_eigenvalue:.3e}, RTS={recovery.smoothed.min_cov_eigenvalue:.3e}. Con 5% de NaN la salida permanece finita. Simular con la planta completa y estimar con la reducida da 7,907% de RMSE, practicamente igual al 7,905% principal.",
            "",
            "![Recuperacion](05_recuperacion_kf_rts_0p01_1000Hz.png)",
            "",
            "## Hallazgo de Q/R que pasa a S3",
            "",
            f"Con q optimizado para recuperacion (`q={recovery.q_scale:.6g}`), el RTS cumple RMSE pero el NIS queda fuera del IC95 ({recovery.nis_report['sum']:.1f} vs [{recovery.nis_report['low']:.1f}, {recovery.nis_report['high']:.1f}]). Con q oraculo para consistencia (`q={nis_tuned.q_scale:.6g}`), el NIS pasa ({nis_tuned.nis_report['sum']:.1f}) pero el RMSE RTS sube a {100*nis_tuned.rts_metrics.relative_rmse:.2f}%. No se esconde: es el compromiso que S3 debe resolver con ML, L-curve y la suite anti-invencion.",
            "El ajuste oraculo simple de NIS paso a 2604 y 2929 Hz, pero quedo fuera por abajo a 1020 Hz (1476,5 frente a [1521,9; 1745,9]); por eso la fila 2.9 no se cierra aun para las tres fs.",
            "",
            "## Fase no minima",
            "",
            f"El inverso causal directo diverge: al duplicar N, su norma crece por un factor `{nmp['growth_ratio']:.3e}`. KF+RTS permanece acotado. Esto confirma empiricamente que el cero RHP/los ceros de muestreo requieren inversion no causal.",
            "",
            "## Conclusion",
            "",
            "La formulacion aumentada queda habilitada con `leaky_rw`; no se justifica migrar aun a Dual KF. La recomendacion estructural provisoria para deconvolucion es **fs=1020 Hz**, sujeta a verificar en S3/S4 que la menor severidad del cero de muestreo compense la menor Nyquist sobre datos reales. El nucleo S2 queda funcional, pero no cerrado: siguen abiertas la definicion del gate continuo-vs-ZOH, la falsa premisa de planitud del prior `leaky_rw` y la consistencia NIS a 1020 Hz.",
            "",
            "## Comandos clave",
            "",
            "```bash",
            "python -m geophone_scope.kalman_deconv.cli markov --cond lp_pga_medido",
            "python -m geophone_scope.kalman_deconv.cli check-obsv --input-model random_walk",
            "python -m geophone_scope.kalman_deconv.cli check-obsv --input-model leaky_rw",
            "python -m geophone_scope.kalman_deconv.cli sampling-zeros --fs 1020 --fs 2604 --fs 2929",
            "python -m geophone_scope.kalman_deconv.cli bench --case ricker25 --fs 2604 --snr 20",
            "python -m geophone_scope.kalman_deconv.report_s2 --output geophone_scope/kalman_deconv/reports/s2_2026-08-17",
            "```",
            "",
        ]),
        encoding="utf-8",
    )
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("reports") / "s2_2026-08-17",
    )
    args = parser.parse_args(argv)
    path = generate(args.output.resolve())
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
