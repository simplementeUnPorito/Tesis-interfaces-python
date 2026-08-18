"""Deconvolucion Kalman + RTS para la cadena geofono -> acondicionador -> ADC.

Modulo de funciones puras: no importa Qt ni FastAPI y no lee capturas. Entra un
``np.ndarray`` mas ``fs``, sale un ``np.ndarray`` mas un diagnostico. La
integracion con la app web es una fase posterior y consiste en una unica funcion
adaptadora.

Contexto, decisiones cerradas y bitacora: ``../HANDOFF_KALMAN.md``.
Tareas y comandos de verificacion: ``../TAREAS_KALMAN.md``.
Bibliografia verificada: ``../REFERENCIAS_KALMAN.md``.
"""

from .models import (
    ConditionerSpec,
    DeconvConfig,
    GeophoneSpec,
    InputModel,
    NoiseSpec,
    PlantSpec,
    ReductionSpec,
)
from .library import (
    list_conditioners,
    list_geophones,
    load_conditioner,
    load_geophone,
    save_conditioner,
    save_geophone,
)
from .plant import compose_plant_zpk, conditioner_zpk, geophone_zpk, zpk_to_modal_ss
from .reduce import (
    FrfCheck,
    prune_near_cancellations,
    prune_rhp_zeros,
    residualize_fast_modes,
    verify_frf,
)
from .discretize import (
    augment_with_input_model,
    build_input_model,
    build_process_noise,
    check_observability,
    discretize_plant,
    markov_parameters,
    prepare_plant,
    sampling_zeros,
)
from .kf import kf_forward, rts_backward
from .synthetic import benchmark_ricker25
from geophone_scope.masw_ridge_kalman import (
    RidgeKalmanConfig,
    RidgeKalmanResult,
    track_dispersion_ridge,
)

__all__ = [
    "ConditionerSpec",
    "DeconvConfig",
    "FrfCheck",
    "GeophoneSpec",
    "InputModel",
    "NoiseSpec",
    "PlantSpec",
    "ReductionSpec",
    "compose_plant_zpk",
    "conditioner_zpk",
    "geophone_zpk",
    "list_conditioners",
    "list_geophones",
    "load_conditioner",
    "load_geophone",
    "prune_near_cancellations",
    "prune_rhp_zeros",
    "residualize_fast_modes",
    "save_conditioner",
    "save_geophone",
    "verify_frf",
    "zpk_to_modal_ss",
    "augment_with_input_model",
    "benchmark_ricker25",
    "build_input_model",
    "build_process_noise",
    "check_observability",
    "discretize_plant",
    "kf_forward",
    "markov_parameters",
    "prepare_plant",
    "rts_backward",
    "sampling_zeros",
    "RidgeKalmanConfig",
    "RidgeKalmanResult",
    "track_dispersion_ridge",
]
