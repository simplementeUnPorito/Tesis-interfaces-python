"""Genera el catalogo congelado de modelos. Se corre a mano, no en runtime.

    python -m geophone_scope.kalman_deconv.build_catalog

Este es el UNICO lugar donde se llama ``np.roots`` sobre los polinomios del
informe de identificacion. El resultado queda guardado en ``data/`` junto con el
hash del archivo fuente, y el modulo lee de ahi. Motivo: los polinomios de
``LP_PGA`` tienen 25 decadas de rango dinamico entre coeficientes, y no conviene
volver a pasar por ellos en cada corrida.

Fuente autoritativa (verificada, leida directamente):
    C:\\Github\\Tesis\\src\\calculos_modelados\\matlab\\AnalisisCircuito\\
        resultados_tanda_calibrada\\05_tablas_reportes\\informe_identificacion.txt

OJO: la tanda del 20/07/2026 (Rbp = 8200 ohm) esta DESCARTADA. Su
``zeta_realizada_componentes = 83,661`` no es un objetivo de diseno.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .library import conditioner_from_num_den, save_conditioner, save_geophone, sha256_of
from .models import ConditionerSpec, GeophoneSpec

INFORME = Path(
    r"C:\Github\Tesis\src\calculos_modelados\matlab\AnalisisCircuito"
    r"\resultados_tanda_calibrada\05_tablas_reportes\informe_identificacion.txt"
)

# Copiados literalmente del informe, en potencias decrecientes de s.
LP_PGA_NUM = [
    0, 0, 0,
    5.595521538e10, 3.420527472e14, 3.836878861e17, 9.033098929e20,
    5.957043684e23, 3.303624364e24, 2.1384983e24, 8.431407583e24,
    -5.18866801e23,
]
LP_PGA_DEN = [
    1, 13513.34939, 343178285.5, 1.343758243e12, 3.189769101e15,
    5.122608358e18, 5.987308998e21, 4.316499374e24, 1.085908459e24,
    1.195293578e25, 1.979867545e23, 1.098802084e24,
]

GEO_LP_NUM = [
    0, 0, 0, 0,
    8.789424678e11, 5.372951988e15, 6.026955222e18, 1.418915862e22,
    9.357302337e24, 5.189321016e25, 3.359145275e25, 1.324402406e26,
    -8.150340651e24, 0,
]
GEO_LP_DEN = [
    1, 13544.76531, 343606767.8, 1.354592855e12, 3.233339325e15,
    5.228122854e18, 6.160833189e21, 4.524819481e24, 1.603296842e26,
    1.708692424e28, 4.662705302e27, 4.719561778e28, 8.161402631e26,
    4.337896755e27,
]


def build() -> None:
    source_hash = sha256_of(INFORME) if INFORME.exists() else ""
    if not source_hash:
        print(f"AVISO: no se encontro {INFORME}; se guarda sin hash de procedencia")

    prov_informe = (
        "informe_identificacion.txt, tanda calibrada 2026-07-21, "
        "src/calculos_modelados/matlab/AnalisisCircuito/resultados_tanda_calibrada"
    )

    # ---------------------------------------------------------------- geofonos
    geophones = [
        GeophoneSpec(
            id="sm24_nominal",
            name="SM-24 nominal (10 Hz, circuito abierto)",
            f_n_hz=10.0,
            zeta=0.25,
            g0_v_s_per_m=28.8,
            form="acceleration",
            provenance="docs/Primera Presentacion/latex/secciones/02b_geofono.tex",
            notes=(
                "El proyecto NO tiene geofonos de 4,5 Hz; el repo lo desmiente en dos "
                "lugares. Bobina 375 ohm a 25 C, masa movil 11 g, frecuencia espuria "
                "> 240 Hz."
            ),
        ),
        GeophoneSpec(
            id="sm24_shunt1339",
            name="SM-24 con shunt de 1339 ohm (zeta = 0,60)",
            f_n_hz=10.0,
            zeta=0.60,
            g0_v_s_per_m=28.8,
            form="acceleration",
            provenance="docs/Primera Presentacion/latex/secciones/02b_geofono.tex",
            notes=(
                "Condicion de calibracion del fabricante. El shunt acopla "
                "amortiguamiento y sensibilidad; si haces barridos de zeta, revisa "
                "si tambien corresponde mover g0."
            ),
        ),
        GeophoneSpec(
            id="jf20dx_ma2023",
            name="JF-20DX de Ma et al. 2023 (caso de control)",
            f_n_hz=10.0,
            zeta=0.3,
            g0_v_s_per_m=27.3,  # 0,273 V/(cm/s) = 27,3 V/(m/s)
            form="acceleration",
            provenance="Ma et al. 2023, Sensors 23:3082, DOI 10.3390/s23063082",
            notes=(
                "Solo para reproducir el paper y validar el constructor de modelos. "
                "Bobina 395 ohm, masa movil 11 g. NO es el geofono del proyecto."
            ),
        ),
    ]

    # -------------------------------------------------------- acondicionadores
    conditioners = [
        ConditionerSpec(
            id="unity",
            name="Passthrough (aisla el efecto del geofono solo)",
            kind="unity",
            gain=1.0,
            valid_band_hz=(1e-6, 1e9),
            id_error_floor=(0.0, 0.0),
            provenance="sintetico",
        ),
        ConditionerSpec(
            id="comp_nominal",
            name="Compensador nominal del proyecto (zeta1 = 937,396)",
            kind="compensator",
            zeta0=0.25,
            zeta1=937.39633,
            w0_rad_s=64.117378,
            k_dc=-3.9705882,
            antialias_hz=392.975168,
            valid_band_hz=(0.001, 2000.0),
            id_error_floor=(0.0, 0.0),
            provenance=prov_informe + " (parametros_compensador.csv)",
            source_sha256=source_hash,
            notes=(
                "Su numerador cancela los polos del geofono por diseno: la cascada "
                "con el SM-24 colapsa a 1 cero / 3 polos y queda plana. "
                "OJO: aplicado a capturas REALES es un desajuste deliberado, porque "
                "el hardware medido no logro esta extension de banda. Se espera que "
                "falle blancura y reconstruccion sobre datos reales; eso es un "
                "resultado, no un bug. Ver HANDOFF_KALMAN.md §5.2."
            ),
        ),
        ConditionerSpec(
            id="comp_ma2023",
            name="Compensador de Ma et al. 2023 (gamma1 = 10)",
            kind="compensator",
            zeta0=0.3,
            zeta1=10.0,
            w0_rad_s=2.0 * np.pi * 10.0,
            k_dc=1.0,
            antialias_hz=None,
            valid_band_hz=(0.1, 200.0),
            id_error_floor=(0.0, 0.0),
            provenance="Ma et al. 2023, Sensors 23:3082",
            notes=(
                "Caso de validacion externa: con el JF-20DX debe reproducir el -3 dB "
                "en 0,8-0,9 Hz y la planitud en 1-100 Hz que reporta el paper."
            ),
        ),
        conditioner_from_num_den(
            LP_PGA_NUM,
            LP_PGA_DEN,
            id="lp_pga_medido",
            name="AFE medido CH4/CH1 (PGA -> ADC), tfest 11/8",
            valid_band_hz=(0.211, 1112.0),
            id_error_floor=(0.1998, 1.161),
            provenance=prov_informe + " (seccion LP_PGA)",
            source_sha256=source_hash,
            notes=(
                "La etapa mejor identificada: coherencia mediana 0,9946, RMS 0,1998 dB "
                "y 1,161 grados. Estado declarado: 'confiable en banda util; dinamica "
                "subsonica no observable'. Tiene 1 cero RHP en +0,0605 rad/s "
                "(0,0096 Hz) y dos cuasi-cancelaciones (258 Hz y 0,26 Hz); el par de "
                "polos en 2728 Hz esta sobre Nyquist en TODAS las fs disponibles."
            ),
        ),
        conditioner_from_num_den(
            GEO_LP_NUM,
            GEO_LP_DEN,
            id="geo_lp_medido",
            name="GEO_LP: Hgeo x LP_PGA ya compuesto (13/9)",
            includes_geophone=True,
            valid_band_hz=(0.211, 1112.0),
            id_error_floor=(0.1998, 1.161),
            provenance=prov_informe + " (seccion GEO_LP)",
            source_sha256=source_hash,
            notes=(
                "NO es una quinta medicion: el informe aclara que compone Hgeo 2/1 "
                "con LP_PGA 11/8. Se usa SOLO para el chequeo cruzado de S1 contra la "
                "composicion explicita. No combinar con un geofono."
            ),
        ),
    ]

    for spec in geophones:
        path = save_geophone(spec, overwrite=True)
        print(f"geofono       {spec.id:20s} -> {path.name}")
    for spec in conditioners:
        path = save_conditioner(spec, overwrite=True)
        n_z, n_p = len(spec.zeros), len(spec.poles)
        extra = f"({n_z} ceros / {n_p} polos)" if spec.kind == "zpk" else f"({spec.kind})"
        print(f"acondicionador {spec.id:20s} -> {path.name:26s} {extra}")

    print(f"\nhash del informe: {source_hash or '(no disponible)'}")


if __name__ == "__main__":
    build()
