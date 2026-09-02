"""MASW inversion: curva de dispersion teorica y busqueda Monte Carlo.

Puerto a NumPy puro de `third-party/maswavespy` (cy_theoretical_dc.pyx +
inversion.py), sin depender de compilar sus extensiones Cython. Dos piezas:

1. `theoretical_dispersion_curve`: curva de dispersion teorica del modo
   fundamental Rayleigh para un medio estratificado elastico-lineal,
   usando el algoritmo fast delta matrix (Buchen & Ben-Hador, 1996).
   Vectorizado sobre (k, c) — la version de referencia evalua escalar por
   escalar en C++; aca se calcula la matriz de signos completa por capa.
   Se agrega normalizacion del vector de recursion por capa (el signo de
   la funcion de dispersion no cambia al escalar por un positivo) para
   evitar overflow de cosh/sinh con k*h grandes.

2. `monte_carlo_inversion`: busqueda global aleatoria del perfil (Vs y
   espesores) que minimiza el desajuste con la curva experimental
   (Olafsdottir et al. 2020, mismo esquema de muestreo que maswavespy).

Referencias
-----------
- Buchen, P.W. & Ben-Hador, R. (1996). Free-mode surface-wave
  computations. Geophysical Journal International 124(3), 869-887.
- Olafsdottir, E.A., Erlingsson, S., Bessason, B. (2020). Open-Source
  MASW Inversion Tool Aimed at Shear Wave Velocity Profiling for Soil
  Site Explorations. Geosciences 10(8), 322.
"""

from __future__ import annotations

import numpy as np


def _csgn(values: np.ndarray) -> np.ndarray:
    """Signo de un numero complejo, como el csgn de la referencia:
    signo de la parte real; si es 0, signo de la parte imaginaria."""
    out = np.sign(values.real)
    zero = out == 0
    if np.any(zero):
        out[zero] = np.sign(values.imag[zero])
    return out


def dispersion_function_signs(
    c_test: np.ndarray,
    k: np.ndarray,
    alpha: np.ndarray,
    beta: np.ndarray,
    rho: np.ndarray,
    h: np.ndarray,
) -> np.ndarray:
    """Matriz de signos de la funcion de dispersion Rayleigh para todos
    los pares (k, c_test). Filas = numeros de onda, columnas = velocidades.

    alpha, beta, rho tienen n+1 elementos (la ultima entrada es el
    semiespacio); h tiene n elementos.
    """
    c_test = np.asarray(c_test, dtype=np.float64).copy()
    k = np.asarray(k, dtype=np.float64)
    alpha = np.asarray(alpha, dtype=np.float64)
    beta = np.asarray(beta, dtype=np.float64)
    rho = np.asarray(rho, dtype=np.float64)
    h = np.asarray(h, dtype=np.float64)
    n = h.size
    if alpha.size != n + 1 or beta.size != n + 1 or rho.size != n + 1:
        raise ValueError("alpha/beta/rho deben tener n+1 elementos y h n elementos")

    # Si un valor de prueba coincide exactamente con una Vs/Vp de capa,
    # s o r se anulan y aparece 0/0; correrlo un epsilon lo evita sin
    # afectar la resolucion del barrido.
    for v in np.concatenate((alpha, beta)):
        hits = c_test == v
        if np.any(hits):
            c_test[hits] += 1e-6 * max(1.0, abs(v))

    c2 = (c_test**2)[None, :]  # (1, n_c)
    kk = k[:, None]  # (n_k, 1)
    alpha2 = alpha**2
    beta2 = beta**2

    G02 = (rho[0] * beta2[0]) ** 2
    shape = (k.size, c_test.size)
    X0 = np.broadcast_to(G02 * (2 * (2 - c2 / beta2[0])), shape).astype(np.complex128).copy()
    X1 = np.broadcast_to(G02 * (-((2 - c2 / beta2[0]) ** 2)), shape).astype(np.complex128).copy()
    X2 = np.zeros(shape, dtype=np.complex128)
    X3 = np.zeros(shape, dtype=np.complex128)
    X4 = np.full(shape, -4 * G02, dtype=np.complex128)

    for q in range(n):
        epsilon = rho[q + 1] / rho[q]
        eta = 2.0 / c2 * (beta2[q] - epsilon * beta2[q + 1])  # (1, n_c)
        a = epsilon + eta
        ak = a - 1.0
        b = 1.0 - eta
        bk = b - 1.0

        r = np.sqrt((1 - c2 / alpha2[q]).astype(np.complex128))  # (1, n_c)
        s = np.sqrt((1 - c2 / beta2[q]).astype(np.complex128))
        krh = kk * r * h[q]  # (n_k, n_c)
        ksh = kk * s * h[q]
        C_alpha = np.cosh(krh)
        S_alpha = np.sinh(krh)
        C_beta = np.cosh(ksh)
        S_beta = np.sinh(ksh)

        p1 = C_beta * X1 + s * S_beta * X2
        p2 = C_beta * X3 + s * S_beta * X4
        p3 = (1.0 / s) * S_beta * X1 + C_beta * X2
        p4 = (1.0 / s) * S_beta * X3 + C_beta * X4

        q1 = C_alpha * p1 - r * S_alpha * p2
        q2 = -(1.0 / r) * S_alpha * p3 + C_alpha * p4
        q3 = C_alpha * p3 - r * S_alpha * p4
        q4 = -(1.0 / r) * S_alpha * p1 + C_alpha * p2

        y1 = ak * X0 + a * q1
        y2 = a * X0 + ak * q2
        z1 = b * X0 + bk * q1
        z2 = bk * X0 + b * q2

        X0 = bk * y1 + b * y2
        X1 = a * y1 + ak * y2
        X2 = epsilon * q3
        X3 = epsilon * q4
        X4 = bk * z1 + b * z2

        # Renormalizar por fila: el signo de D es invariante ante un factor
        # positivo, y sin esto cosh/sinh desbordan con k*h grandes.
        scale = np.maximum.reduce([np.abs(X0), np.abs(X1), np.abs(X2), np.abs(X3), np.abs(X4)])
        scale[scale == 0] = 1.0
        X0 /= scale
        X1 /= scale
        X2 /= scale
        X3 /= scale
        X4 /= scale

    r = np.sqrt((1 - c2 / alpha2[n]).astype(np.complex128))
    s = np.sqrt((1 - c2 / beta2[n]).astype(np.complex128))
    D = X1 + s * X2 - r * (X3 + s * X4)
    return _csgn(D)


def theoretical_dispersion_curve(
    c_test: np.ndarray,
    wavelengths: np.ndarray,
    alpha: np.ndarray,
    beta: np.ndarray,
    rho: np.ndarray,
    h: np.ndarray,
    delta_c: float = 5.0,
) -> np.ndarray:
    """Curva de dispersion teorica del modo fundamental: para cada longitud
    de onda, la menor velocidad de `c_test` donde la funcion de dispersion
    cambia de signo. NaN donde no se encontro raiz en el rango.

    Misma logica de barrido que compute_fdma de maswavespy: en la longitud
    de onda i el barrido arranca en la raiz anterior menos `delta_c` (las
    longitudes de onda se procesan en orden ascendente).
    """
    c_test = np.asarray(c_test, dtype=np.float64)
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    order = np.argsort(wavelengths)
    k = 2.0 * np.pi / wavelengths[order]
    signs = dispersion_function_signs(c_test, k, alpha, beta, rho, h)

    c_step = float(c_test[1] - c_test[0]) if c_test.size > 1 else 1.0
    delta_m = int(round(delta_c / c_step))
    c_t_sorted = np.full(wavelengths.size, np.nan)
    m_loc = 0
    for i in range(wavelengths.size):
        row = signs[i]
        m_root = None
        if row[0] * row[m_loc] == -1:
            m_root = m_loc
        else:
            seg = row[m_loc:]
            changes = np.flatnonzero(seg[:-1] * seg[1:] == -1)
            if changes.size:
                m_root = m_loc + int(changes[0]) + 1
        if m_root is not None:
            c_t_sorted[i] = c_test[m_root]
            m_loc = max(0, m_root - delta_m)
        else:
            m_loc = 0
    c_t = np.empty_like(c_t_sorted)
    c_t[order] = c_t_sorted
    return c_t


def dispersion_misfit(c_obs: np.ndarray, c_t: np.ndarray) -> float:
    """Desajuste [%] entre curva experimental y teorica (misma formula que
    maswavespy). Los puntos sin raiz teorica (NaN) invalidan la corrida si
    son mas de la mitad; si no, se promedia sobre los validos."""
    c_obs = np.asarray(c_obs, dtype=np.float64)
    c_t = np.asarray(c_t, dtype=np.float64)
    valid = np.isfinite(c_t)
    # Se exige al menos la mitad de los puntos; para N impar esto significa
    # ceil(N/2), no floor(N/2). Ej.: 2/3 y 3/5 son válidos, 1/3 y 2/5 no.
    minimum_valid = max(1, (c_obs.size + 1) // 2)
    if valid.sum() < minimum_valid:
        return float("inf")
    return float(np.mean(np.abs(c_obs[valid] - c_t[valid]) / c_obs[valid]) * 100.0)


def alpha_from_beta(beta: np.ndarray, nu: float) -> np.ndarray:
    """Velocidad P a partir de Vs y el modulo de Poisson (suelo no
    saturado, igual que maswavespy con n_unsat = todas las capas)."""
    return np.sqrt((2.0 * (1.0 - nu)) / (1.0 - 2.0 * nu)) * np.asarray(beta, dtype=np.float64)


def initial_model_from_dc(
    freqs: np.ndarray,
    c_obs: np.ndarray,
    n_layers: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Modelo inicial (beta, h) a partir de la curva picada, con la regla
    empirica Vs(z) ~ 1.09 * c(lambda = 2z) (Olafsdottir et al. 2020).

    Devuelve beta (n_layers+1, la ultima entrada es el semiespacio) y
    h (n_layers).
    """
    freqs = np.asarray(freqs, dtype=np.float64)
    c_obs = np.asarray(c_obs, dtype=np.float64)
    lam = c_obs / freqs
    z_max = float(np.max(lam)) / 2.0
    # Espesores crecientes con la profundidad (mas resolucion arriba).
    weights = np.arange(1, n_layers + 1, dtype=np.float64)
    h = z_max * weights / weights.sum()
    boundaries = np.concatenate(([0.0], np.cumsum(h)))
    mids = 0.5 * (boundaries[:-1] + boundaries[1:])
    order = np.argsort(lam)
    lam_sorted = lam[order]
    c_sorted = c_obs[order]
    beta_layers = 1.09 * np.interp(2.0 * mids, lam_sorted, c_sorted)
    beta_half = 1.09 * float(c_sorted[-1])
    beta = np.concatenate((beta_layers, [max(beta_half, float(beta_layers[-1]))]))
    return beta, h


def monte_carlo_inversion(
    freqs: np.ndarray,
    c_obs: np.ndarray,
    n_layers: int = 4,
    n_iterations: int = 1000,
    bs: float = 5.0,
    bh: float = 10.0,
    nu: float = 0.35,
    rho: float = 1850.0,
    reversals: int = 0,
    c_test_step: float = 1.0,
    delta_c: float = 5.0,
    beta_initial: np.ndarray | None = None,
    h_initial: np.ndarray | None = None,
    seed: int | None = None,
    progress_cb=None,
) -> dict:
    """Inversion Monte Carlo del perfil Vs (mismo esquema que
    maswavespy.mc_initiation): en cada iteracion se perturban Vs (+-bs%)
    y espesores (+-bh%) alrededor del mejor modelo hasta ahora, se calcula
    la curva teorica y se acepta si el desajuste no empeora.

    progress_cb(iteracion, total, mejor_desajuste, mejor_modelo) se llama
    con iteracion=0 para el modelo inicial y luego cada ~25 iteraciones;
    mejor_modelo es un dict {"beta", "h", "c_t"} con el mejor perfil hasta
    ese momento (para dibujar el loop de inversion en vivo). Si devuelve
    False la busqueda se corta ahi.

    Devuelve dict con beta/h/alpha/c_t del mejor modelo, su desajuste,
    la curva inicial y el historial de desajustes por iteracion.
    """
    freqs = np.asarray(freqs, dtype=np.float64)
    c_obs = np.asarray(c_obs, dtype=np.float64)
    if freqs.size < 3:
        raise ValueError("Se necesitan al menos 3 puntos picados de la curva de dispersion")
    wavelengths = c_obs / freqs

    if beta_initial is None or h_initial is None:
        beta_initial, h_initial = initial_model_from_dc(freqs, c_obs, n_layers)
    beta_initial = np.asarray(beta_initial, dtype=np.float64)
    h_initial = np.asarray(h_initial, dtype=np.float64)
    n = h_initial.size
    rho_vec = np.full(n + 1, float(rho))

    c_lo = max(10.0, 0.4 * float(np.min(c_obs)))
    c_hi = 1.2 * float(np.max(beta_initial)) * (1.0 + bs / 100.0) ** 3
    c_hi = max(c_hi, 1.5 * float(np.max(c_obs)))
    c_test = np.arange(c_lo, c_hi, float(c_test_step))

    rng = np.random.default_rng(seed)

    def forward(beta_arr: np.ndarray, h_arr: np.ndarray) -> np.ndarray:
        alpha_arr = alpha_from_beta(beta_arr, nu)
        return theoretical_dispersion_curve(c_test, wavelengths, alpha_arr, beta_arr, rho_vec, h_arr, delta_c=delta_c)

    c_t_initial = forward(beta_initial, h_initial)
    e_opt = dispersion_misfit(c_obs, c_t_initial)
    beta_opt = beta_initial.copy()
    h_opt = h_initial.copy()
    c_t_opt = c_t_initial.copy()
    history = np.empty(n_iterations)

    def snapshot() -> dict:
        return {"beta": beta_opt.copy(), "h": h_opt.copy(), "c_t": c_t_opt.copy()}

    iterations_to_run = n_iterations
    if progress_cb is not None:
        if progress_cb(0, n_iterations, e_opt, snapshot()) is False:
            # Cancelación antes de muestrear: devolver el modelo inicial y un
            # historial vacío, tal como promete el contrato del callback.
            iterations_to_run = 0
            history = history[:0]

    for w in range(iterations_to_run):
        beta_test = beta_opt + rng.uniform(-(bs / 100.0) * beta_opt, (bs / 100.0) * beta_opt)
        tries = 0
        while np.any(beta_test[reversals:] != np.sort(beta_test[reversals:])) and tries < 50:
            beta_test = beta_opt + rng.uniform(-(bs / 100.0) * beta_opt, (bs / 100.0) * beta_opt)
            tries += 1
        if tries >= 50:
            # ``reversals`` libera sólo las interfaces superficiales. El
            # fallback debe ordenar el sufijo restringido, no el perfil entero,
            # o elimina silenciosamente las inversiones solicitadas.
            beta_test[reversals:] = np.sort(beta_test[reversals:])
        h_test = h_opt + rng.uniform(-(bh / 100.0) * h_opt, (bh / 100.0) * h_opt)
        h_test = np.maximum(h_test, 0.1)

        c_t = forward(beta_test, h_test)
        e_test = dispersion_misfit(c_obs, c_t)
        if e_test <= e_opt:
            e_opt = e_test
            beta_opt = beta_test
            h_opt = h_test
            c_t_opt = c_t
        history[w] = e_opt

        if progress_cb is not None and (w % 25 == 0 or w == n_iterations - 1):
            if progress_cb(w + 1, n_iterations, e_opt, snapshot()) is False:
                history = history[: w + 1]
                break

    return {
        "beta": beta_opt,
        "h": h_opt,
        "alpha": alpha_from_beta(beta_opt, nu),
        "rho": rho_vec,
        "c_t": c_t_opt,
        "misfit": e_opt,
        "wavelengths": wavelengths,
        "freqs": freqs,
        "c_obs": c_obs,
        "beta_initial": beta_initial,
        "h_initial": h_initial,
        "c_t_initial": c_t_initial,
        "history": history,
        "nu": nu,
    }
