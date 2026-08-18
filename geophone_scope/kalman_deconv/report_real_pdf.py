"""PDF visual de la corrida real RTS, waterfall y MASW de Canchita grupo 1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


REPO_ROOT = Path(__file__).resolve().parents[5]
RESULTS = Path(__file__).resolve().parent / "reports" / "real_canchita_group1"
DEFAULT_OUTPUT = REPO_ROOT / "output" / "pdf" / "RTS_REAL_WATERFALL_MASW_CANCHITA.pdf"
DEFAULT_TEMP = REPO_ROOT / "tmp" / "pdfs" / "rts_real_canchita"


def _plot_waterfalls(data: np.lib.npyio.NpzFile, output: Path) -> Path:
    time = data["time_s"]
    distances = data["distances_m"]
    arms = [
        ("Aceleracion medida\n(salida Ma, V)", data["measured_acceleration_ma_output_v"], "#4c78a8"),
        ("Medida + SOS filtfilt\nButterworth orden 10, 0-80 Hz", data["measured_acceleration_sos_v"], "#f58518"),
        ("Aceleracion reconstruida\nKalman + RTS", data["rts_acceleration"], "#54a24b"),
    ]
    view = (time >= -0.12) & (time <= 1.25)
    pre = (time >= -0.12) & (time < -0.03)
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 7.6), sharex=True, sharey=True)
    for ax, (title, matrix, color) in zip(axes, arms):
        centered = matrix - np.nanmedian(matrix[:, pre], axis=1, keepdims=True)
        scale = np.nanpercentile(np.abs(centered[:, view]), 99.0, axis=1)
        scale = np.maximum(scale, np.finfo(float).tiny)
        for distance, trace, norm in zip(distances, centered, scale):
            normalized = trace[view] / norm
            ax.plot(time[view], distance + 0.72 * normalized, color=color, lw=0.70)
        ax.axvline(0.0, color="0.35", lw=0.9, ls=":")
        ax.axhline(30.0, color="#b279a2", lw=0.9, ls="--")
        ax.set_title(title, fontsize=12, weight="bold")
        ax.set_xlabel("Tiempo desde el golpe [s]")
        ax.set_xlim(-0.12, 1.25)
        ax.grid(True, alpha=0.18)
    axes[0].set_ylabel("Offset [m] (trazas normalizadas individualmente)")
    axes[0].invert_yaxis()
    fig.suptitle(
        "Waterfall real - Canchita grupo 1 - 21 offsets, 10 a 50 m, fs=1020 Hz",
        fontsize=15,
        weight="bold",
    )
    fig.text(0.5, 0.015, "Linea violeta: traza representativa de 30 m. Linea vertical: tiempo del golpe.", ha="center")
    fig.tight_layout(rect=(0.02, 0.045, 1, 0.94))
    path = output / "waterfall_medida_sos_rts.png"
    fig.savefig(path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _image_for_page(path: Path, max_width: float, max_height: float) -> Image:
    from PIL import Image as PilImage

    with PilImage.open(path) as source:
        width_px, height_px = source.size
    ratio = min(max_width / width_px, max_height / height_px)
    return Image(str(path), width=width_px * ratio, height=height_px * ratio)


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PdfTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=24, leading=28, textColor=colors.HexColor("#17324D"),
            alignment=TA_CENTER, spaceAfter=8 * mm,
        ),
        "subtitle": ParagraphStyle(
            "PdfSubtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=12, leading=16, textColor=colors.HexColor("#3E5366"),
            alignment=TA_CENTER, spaceAfter=7 * mm,
        ),
        "h1": ParagraphStyle(
            "PdfH1", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=17, leading=21, textColor=colors.HexColor("#17324D"),
            spaceAfter=4 * mm,
        ),
        "body": ParagraphStyle(
            "PdfBody", parent=base["BodyText"], fontName="Helvetica",
            fontSize=10.2, leading=14, textColor=colors.HexColor("#263746"),
            alignment=TA_LEFT, spaceAfter=3 * mm,
        ),
        "caption": ParagraphStyle(
            "PdfCaption", parent=base["BodyText"], fontName="Helvetica-Oblique",
            fontSize=8.8, leading=12, textColor=colors.HexColor("#536777"),
            alignment=TA_CENTER, spaceBefore=3 * mm,
        ),
        "result": ParagraphStyle(
            "PdfResult", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=12, leading=16, textColor=colors.HexColor("#245A42"),
            alignment=TA_CENTER, spaceBefore=4 * mm, spaceAfter=4 * mm,
        ),
    }


def _table(data, widths=None) -> Table:
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="CENTER")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF3F6")]),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#AAB8C2")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _page_decorator(canvas, doc):
    canvas.saveState()
    width, height = landscape(A4)
    canvas.setStrokeColor(colors.HexColor("#CBD5DC"))
    canvas.setLineWidth(0.5)
    canvas.line(14 * mm, 11 * mm, width - 14 * mm, 11 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#607483"))
    canvas.drawString(14 * mm, 7 * mm, "Canchita grupo 1 - RTS real, waterfall y MASW")
    canvas.drawRightString(width - 14 * mm, 7 * mm, f"Pagina {doc.page}")
    canvas.restoreState()


def build_pdf(output: Path = DEFAULT_OUTPUT, temp: Path = DEFAULT_TEMP) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    data = np.load(RESULTS / "real_gathers_and_dispersion.npz")
    metrics = json.loads((RESULTS / "metrics.json").read_text(encoding="utf-8"))
    waterfall = _plot_waterfalls(data, temp)
    styles = _styles()
    page_width, page_height = landscape(A4)
    usable_width = page_width - 30 * mm
    usable_height = page_height - 33 * mm

    document = SimpleDocTemplate(
        str(output), pagesize=landscape(A4),
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=17 * mm,
        title="RTS real, waterfall y MASW - Canchita grupo 1",
        author="Proyecto Tesis",
        subject="Comparacion aceleracion medida, SOS filtfilt, RTS y picking Kalman",
    )
    story = []

    story.extend([
        Spacer(1, 9 * mm),
        Paragraph("RTS real, waterfall y MASW", styles["title"]),
        Paragraph("Canchita grupo 1 - aceleraciones medidas con la cadena de Ma et al.", styles["subtitle"]),
        _table([
            ["Datos reales", "Adquisicion", "Comparacion", "Referencia externa"],
            ["21 offsets: 10-50 m", "fs = 1020 Hz", "SOS filtfilt vs Kalman+RTS", "132 picks: 8.0-29.83 Hz"],
        ], [46 * mm, 42 * mm, 63 * mm, 55 * mm]),
        Spacer(1, 8 * mm),
        Paragraph(
            "Las capturas son mediciones de aceleracion realizadas con el circuito de Ma et al. "
            "El repositorio almacena la salida electrica de esa cadena en voltios. RTS estima la "
            "aceleracion de entrada en m/s^2; por ello las PSD se normalizan antes de comparar su forma.",
            styles["body"],
        ),
        Paragraph(
            "Resultado principal: el picker Kalman+RTS reduce el RMSE de 138.15 a 16.25 m/s "
            "sobre la imagen SOS. Aplicar RTS a las trazas antes del MASW no aporta una mejora "
            "adicional: el RMSE queda en 16.45 m/s.",
            styles["result"],
        ),
        Paragraph(
            "Contenido: waterfall de los tres brazos, traza y PSD de 30 m, imagen MASW SOS, "
            "imagen MASW tras RTS, picks superpuestos y tabla final de metricas.",
            styles["body"],
        ),
        PageBreak(),
    ])

    story.extend([
        Paragraph("1. Waterfall real: medicion, SOS y RTS", styles["h1"]),
        _image_for_page(waterfall, usable_width, usable_height - 23 * mm),
        Paragraph(
            "Cada traza se normaliza por separado para visualizar fase, llegada y continuidad espacial. "
            "La normalizacion no se usa en el filtro ni en el MASW.", styles["caption"],
        ),
        PageBreak(),
    ])

    pages = [
        ("2. Traza real y contenido espectral a 30 m", RESULTS / "01_traza_real_y_espectro.png",
         "Arriba: formas de onda normalizadas. Abajo: PSD con area unitaria entre 1 y 200 Hz."),
        ("3. MASW usando solamente SOS filtfilt", RESULTS / "02_dispersion_sos_filtfilt.png",
         "Cian: referencia hidrogeologicamente guiada. Blanco: auto-pick actual. Verde: tracker Kalman+RTS."),
        ("4. MASW despues de reconstruir las trazas con RTS", RESULTS / "02_dispersion_rts_real.png",
         "Se aplica exactamente el mismo metodo phase-shift y el mismo tracker que en el brazo SOS."),
        ("5. Comparacion cuantitativa de los picks", RESULTS / "04_comparacion_picking.png",
         "La mejora dominante proviene del tracker; la deconvolucion RTS previa no reduce el error adicionalmente."),
    ]
    for title, image_path, caption in pages:
        story.extend([
            Paragraph(title, styles["h1"]),
            _image_for_page(image_path, usable_width, usable_height - 23 * mm),
            Paragraph(caption, styles["caption"]),
            PageBreak(),
        ])

    picking = metrics["picking_metrics"]
    spectral = metrics["spectral_metrics_representative_30m"]
    pick_rows = [["Brazo", "RMSE [m/s]", "MAE [m/s]", "Sesgo [m/s]", "Cobertura"]]
    for name, values in picking.items():
        pick_rows.append([
            name,
            f"{values['rmse_m_s']:.3f}",
            f"{values['mae_m_s']:.3f}",
            f"{values['bias_m_s']:.3f}",
            f"{100 * values['coverage_fraction']:.1f}%",
        ])
    spectral_rows = [["Salida", "1-10 Hz", "10-50 Hz", "50-80 Hz", "80-200 Hz", "Centroide"]]
    for name, values in spectral.items():
        spectral_rows.append([
            name,
            f"{100 * values['fraction_1_10_hz']:.3f}%",
            f"{100 * values['fraction_10_50_hz']:.3f}%",
            f"{100 * values['fraction_50_80_hz']:.3f}%",
            f"{100 * values['fraction_80_200_hz']:.3f}%",
            f"{values['centroid_1_200_hz']:.2f} Hz",
        ])
    story.extend([
        Paragraph("6. Resultados numericos y conclusion", styles["h1"]),
        Paragraph("Picking contra la referencia hidrogeologica", styles["body"]),
        _table(pick_rows, [62 * mm, 30 * mm, 30 * mm, 30 * mm, 28 * mm]),
        Spacer(1, 7 * mm),
        Paragraph("Distribucion espectral normalizada de la traza de 30 m", styles["body"]),
        _table(spectral_rows, [62 * mm, 23 * mm, 25 * mm, 25 * mm, 27 * mm, 29 * mm]),
        Spacer(1, 7 * mm),
        KeepTogether([
            Paragraph(
                "Conclusion: el picker Kalman mejora 88.2% el RMSE sobre SOS y elimina el salto "
                "del auto-pick al modo de 230-245 m/s. RTS conserva 98.60% de la PSD normalizada "
                "en 10-50 Hz y desplaza el centroide de 22.83 a 26.38 Hz, pero no mejora el picking "
                "frente al tracker aplicado directamente sobre SOS.", styles["result"],
            ),
            Paragraph(
                "Limite de interpretacion: no hay una aceleracion de referencia independiente y "
                "sincronizada para calcular RMSE temporal de la corrida real. La curva hidro se usa "
                "solo para evaluar el picking y nunca para ajustar el tracker.", styles["body"],
            ),
        ]),
    ])

    document.build(story, onFirstPage=_page_decorator, onLaterPages=_page_decorator)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--temp", type=Path, default=DEFAULT_TEMP)
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()
    output = build_pdf(args.output.resolve(), args.temp.resolve())
    print(output)
    if not args.keep_temp:
        shutil.rmtree(args.temp.resolve(), ignore_errors=True)


if __name__ == "__main__":
    main()
