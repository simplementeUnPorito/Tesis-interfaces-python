"""Configuracion del estimador, en dataclasses.

Regla del modulo: **todo lo que vaya a ser un checkbox o un combo en la interfaz
nace como campo de una dataclass**, no como un ``if`` adentro de una funcion. La
integracion web (S5) tiene que reducirse a exponer estos campos.

Unidades, declaradas de una vez:

- ``u`` (la entrada que se estima) esta en m/s^2 cuando la magnitud es
  aceleracion, y en m/s cuando es velocidad.
- ``y`` (la medicion) esta en volts, o en cuentas del ADC si se pasa
  ``adc_scale_v_per_count``.
- Los estados quedan adimensionalizados por el escalado interno de la
  realizacion; no tienen significado fisico directo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Sequence

import numpy as np

# Formas del modelo del geofono. Ver REFERENCIAS_KALMAN.md §1.1.
#   "acceleration": H_a(s) = -G*s   / (s^2 + 2*zeta*w0*s + w0^2)   <- la que se usa
#   "velocity":     H_v(s) = -G*s^2 / (s^2 + 2*zeta*w0*s + w0^2)
GeophoneForm = Literal["acceleration", "velocity"]

# Como esta descrito un acondicionador en el catalogo.
#   "zpk":         ceros/polos/ganancia explicitos (asi se guarda el medido)
#   "compensator": parametrico, se construye de zeta0/zeta1/w0/k_dc/antialias
#   "unity":       passthrough, para aislar el efecto del geofono solo
ConditionerKind = Literal["zpk", "compensator", "unity"]


@dataclass(frozen=True)
class GeophoneSpec:
    """Modelo del transductor.

    El default es el SM-24 del proyecto: 10 Hz, no 4,5 Hz. El repo desmiente el
    4,5 Hz en dos lugares; ver HANDOFF_KALMAN.md §5.1.
    """

    id: str = "sm24_nominal"
    name: str = "SM-24 nominal (10 Hz, circuito abierto)"
    f_n_hz: float = 10.0
    zeta: float = 0.25
    g0_v_s_per_m: float = 28.8
    form: GeophoneForm = "acceleration"
    # Reemplaza la constante del numerador. Sirve para desacoplar la ganancia del
    # amortiguamiento cuando se importa un modelo escrito en la normalizacion del
    # informe (donde el numerador es zeta*w0*s y por lo tanto contiene zeta).
    gain_override: Optional[float] = None
    provenance: str = ""
    notes: str = ""

    @property
    def w0(self) -> float:
        return 2.0 * np.pi * self.f_n_hz


@dataclass(frozen=True)
class ConditionerSpec:
    """Modelo del acondicionamiento analogico (del geofono al ADC)."""

    id: str = "lp_pga_medido"
    name: str = ""
    kind: ConditionerKind = "zpk"

    # kind == "zpk"
    zeros: Sequence[complex] = ()
    poles: Sequence[complex] = ()
    gain: float = 1.0

    # kind == "compensator": (s^2+2*z0*w0*s+w0^2)/(s^2+2*z1*w0*s+w0^2) * k_dc/(1+s*tau)
    zeta0: float = 0.25
    zeta1: float = 937.39633
    w0_rad_s: float = 64.117378
    k_dc: float = -3.9705882
    antialias_hz: Optional[float] = 392.975168

    # Banda donde el modelo es defendible. Fuera de aca el estimador no debe
    # amplificar: la identificacion no es valida y el resultado no es reportable.
    valid_band_hz: tuple[float, float] = (0.211, 1112.0)
    # Piso de error de la propia identificacion (dB RMS, grados RMS). No tiene
    # sentido exigirle a la realizacion mas precision que esto.
    id_error_floor: tuple[float, float] = (0.1998, 1.161)
    # True cuando la entrada del catalogo ya incluye al geofono (caso GEO_LP).
    # Solo se usa para el chequeo cruzado de S1; no combinar con un geofono.
    includes_geophone: bool = False

    provenance: str = ""
    source_sha256: str = ""
    notes: str = ""


@dataclass(frozen=True)
class PlantSpec:
    """Modelo directo: movimiento del suelo -> volts en la entrada del ADC."""

    geophone: GeophoneSpec = field(default_factory=GeophoneSpec)
    conditioner: ConditionerSpec = field(default_factory=ConditionerSpec)
    include_geophone: bool = True
    include_conditioner: bool = True
    # Magnitud del suelo que se estima, o sea cual es la entrada ``u`` del modelo
    # directo. "acceleration" es la nativa del catalogo (H_a = Y/A_ground).
    #
    # Pedir otra magnitud NO agrega un integrador: como a = s*v = s^2*d, la
    # planta correcta es H_v = s*H_a y H_d = s^2*H_a, o sea **ceros en el
    # origen**. Cada cero extra baja el grado relativo en uno y agrega un modo
    # que la medicion no observa en DC ("q" de Maes et al. 2016 §2.3), asi que
    # con "velocity" y "displacement" hay que re-verificar la observabilidad del
    # par aumentado (TEST 2) en vez de heredar el resultado del caso aceleracion.
    #
    # La incertidumbre sigue saliendo de la covarianza y no de integrar despues:
    # ``u`` ES la magnitud pedida, estimada dentro del filtro.
    estimate: Literal["acceleration", "velocity", "displacement"] = "acceleration"
    adc_scale_v_per_count: float = 1.0


@dataclass(frozen=True)
class InputModel:
    """Modelo estocastico de u_k, el movimiento del suelo.

    El default NO es random walk puro, y no es un detalle de tuning: con el cero
    del geofono en s=0, un random walk (polo en z=1) cancela contra ese cero y el
    modo DC queda inobservable. El resultado es deriva de baja frecuencia que el
    RTS suaviza hasta que parece senal sismica de 1-2 Hz. Ver HANDOFF §6,
    Obstruccion 2.
    """

    kind: Literal["random_walk", "leaky_rw", "wiener2", "ou_band"] = "leaky_rw"
    q_scale: float = 1.0
    leak_hz: float = 0.7
    band_hz: Optional[tuple[float, float]] = None  # solo para "ou_band"


@dataclass(frozen=True)
class NoiseSpec:
    r_var: Optional[float] = None  # None -> estimar de la ventana pre-arribo
    pre_arrival_s: float = 0.5
    r_method: Literal["mad", "var", "welch_hf"] = "mad"
    # Ruido de proceso sobre los estados de planta. Default 0: la planta se toma
    # como deterministica y su incertidumbre vive en el modelo de entrada.
    q_plant_scale: float = 0.0


@dataclass(frozen=True)
class ReductionSpec:
    enabled: bool = True
    band_hz: tuple[float, float] = (1.0, 150.0)
    # |z - p| / |p| por debajo del cual se declara cuasi-cancelacion.
    #
    # 5e-3 y no 5e-2, y el motivo no es estetico. Los artefactos reales del
    # ajuste de LP_PGA tienen delta relativo 1,5e-4 (par de 258 Hz) y 2e-3 (par
    # de 0,26 Hz). En cambio, la cascada nominal deja un par polo-cero con delta
    # 2,05e-2 que **no es artefacto**: es la desintonia entre el geofono nominal
    # (10,0 Hz) y el compensador, que esta sintonizado a 10,2046 Hz. Podar ese
    # par cuesta 0,26 dB y 4,2 grados en la banda util. Con 5e-3 se podan los
    # artefactos y se conserva la fisica.
    cancel_tol_ratio: float = 0.005
    # Residualiza los modos por encima de este valor. None -> 0.4 * Nyquist.
    residualize_above_rad_s: Optional[float] = None
    # Poda los ceros del semiplano derecho por debajo de esta frecuencia. El
    # limite natural es el borde inferior de validez del modelo (0,211 Hz): un
    # cero RHP tres decadas por debajo de la banda util es artefacto del ajuste,
    # no fisica. Nunca se poda en silencio: queda en el log.
    prune_rhp_below_hz: Optional[float] = 0.211
    tol_db: float = 0.3
    tol_deg: float = 2.0


@dataclass(frozen=True)
class DeconvConfig:
    """Configuracion completa de una corrida.

    ``fs`` no tiene default a proposito. Si la fs de la discretizacion no es la
    fs real de la senal, el modelo esta mal y todo lo demas es ruido con formato
    lindo. Ojo con ``resample_signal``, que usa ``limit_denominator(1000)``: la
    fs efectiva puede no ser exactamente la pedida, y la que vale es la real.
    """

    fs: float
    plant: PlantSpec = field(default_factory=PlantSpec)
    input_model: InputModel = field(default_factory=InputModel)
    noise: NoiseSpec = field(default_factory=NoiseSpec)
    reduction: ReductionSpec = field(default_factory=ReductionSpec)
    disc_method: Literal["zoh", "bilinear", "foh", "impulse"] = "zoh"
    smoother: bool = True
    # El notch armonico va ANTES del KF: la red de 50 Hz cae dentro de la banda
    # util y no es un IIR sino una resta por minimos cuadrados, asi que no
    # colorea el ruido ni introduce fase.
    pre_notch: bool = True
    # "post" (default): KF sobre la senal cruda y Butterworth despues, solo para
    # graficar. "pre" emite warning y marca R como no confiable. Ver HANDOFF §8.
    butter_stage: Literal["off", "post", "pre"] = "post"

    def __post_init__(self) -> None:
        if not np.isfinite(self.fs) or self.fs <= 0:
            raise ValueError(f"fs debe ser un numero positivo finito, no {self.fs!r}")

    @property
    def dt(self) -> float:
        return 1.0 / float(self.fs)

    @property
    def nyquist_hz(self) -> float:
        return 0.5 * float(self.fs)
