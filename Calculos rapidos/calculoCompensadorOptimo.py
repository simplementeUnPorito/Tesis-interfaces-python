# -*- coding: utf-8 -*-
"""
calculoCompensadorOptimo.py
Búsqueda inteligente usando scipy differential_evolution + paralelismo.

Espacio continuo 3-D: [log10(R1), log10(C1), log10(C2)]
  → R2 determinado por condición ω0 exacta
  → Ratios adder optimizados analíticamente (lstsq)
  → Snap a comerciales + T-redes al final

Paralelismo:
  • Fase 1 (DE global):      workers = N_WORKERS (todos los núcleos)
  • Fase 2 (bandas de ζ):    ProcessPoolExecutor — cada banda en un proceso
  • Fase 3 (snap+adder):     ThreadPoolExecutor  — I/O-bound, GIL libre en numpy
"""
import sys, os, time, csv
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import warnings
warnings.filterwarnings('ignore')
from functools import partial
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from scipy.optimize import differential_evolution, minimize

try:
    from tqdm import tqdm
except ImportError:
    class tqdm:
        def __init__(self, iterable=None, total=None, desc='', unit='it', ncols=None, **kw):
            self._it = list(iterable) if iterable is not None else []
            self._tot = total if total is not None else len(self._it)
            self._n = 0; self._t0 = time.time(); self._desc = desc; self._last = -1
        def __iter__(self):
            for item in self._it:
                yield item
                self._n += 1
                pct = int(100*self._n/self._tot) if self._tot else 0
                if pct != self._last and pct % 5 == 0:
                    el = time.time()-self._t0
                    eta = el/self._n*(self._tot-self._n) if self._n else 0
                    print(f"\r  {self._desc}: {pct}% [{self._n}/{self._tot}] "
                          f"elapsed {el:.0f}s ETA {eta:.0f}s  ", end='', flush=True)
                    self._last = pct
            print(flush=True)
        def set_postfix_str(self, s): pass
        def update(self, n=1): pass
        def close(self): print(flush=True)
        def __enter__(self): return self
        def __exit__(self, *a): pass

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ════════════════════════════════════════════════════════════════════════════

# ── Geófono ──────────────────────────────────────────────────────────────────
f0_des      = 10
ZETA_GEO    = 0.25
ZETA_TARGET = 1000
omega0      = 2*np.pi*f0_des

# ── Op-amp BP ─────────────────────────────────────────────────────────────────
A0_dB_bp    = 90
A0_bp       = 10**(A0_dB_bp/20.0)
f_p_bp      = 8e6
omega_p_bp  = 2*np.pi*f_p_bp
Rin_bp_amp  = 35e6

# ── Op-amp Adder ──────────────────────────────────────────────────────────────
A0_dB_add   = 90
A0_add      = 10**(A0_dB_add/20.0)
f_p_add     = 8e6
omega_p_add = 2*np.pi*f_p_add
Rin_add     = 35e6

# ── Rangos de búsqueda ───────────────────────────────────────────────────────
R_min_bp    = 1e3
R_max_bp    = 1e6
R_min_add   = 1e3
R_max_add   = 1e6
F0_TOL_PCT  = 10.0
ZETA_MIN    = 100
ZETA_MAX    = 1000
TOP_N       = 15

# ── Parámetros del optimizador ───────────────────────────────────────────────
N_WORKERS          = os.cpu_count() or 1  # núcleos totales
RAM_LIMIT_GB       = 20    # RAM máxima para procesos paralelos (GB)
RAM_PER_PROC_GB    = 1.5   # RAM estimada por proceso worker
# Máximo de procesos simultáneos sin pasarse del límite de RAM
MAX_PROC_WORKERS   = max(1, min(N_WORKERS, int(RAM_LIMIT_GB / RAM_PER_PROC_GB)))
SNAP_CHUNK_SIZE    = 5_000  # combos por chunk en Fase 3 (controla RAM del pool)
N_GEN_DE           = 500   # generaciones differential_evolution
POPSIZE_DE         = 50    # individuos / generación  (total: POPSIZE × n_vars)
N_RESTARTS_BAND    = 50    # restarts multi-start por banda de ζ
N_ITER_LOCAL       = 500   # iteraciones máx. por minimización local
N_SNAP_NEIGHBORS   = 50     # vecinos comerciales ±N alrededor del óptimo

# ── Frecuencias ───────────────────────────────────────────────────────────────
FREQS_EVAL = np.logspace(np.log10(f0_des/1000), np.log10(1e5), 800)

# ════════════════════════════════════════════════════════════════════════════
# VALORES COMERCIALES
# ════════════════════════════════════════════════════════════════════════════

RES_COMERCIALES = sorted([
    1,1.2,1.5,1.6,2,2.2,2.4,2.7,3,3.3,3.6,3.9,4.3,4.7,5.1,5.6,6.2,6.8,7.5,8.2,9.1,
    10,12,15,18,20,22,24,27,30,33,36,39,43,47,51,56,62,68,75,82,91,
    100,120,150,180,200,220,240,270,300,330,360,390,430,470,510,560,620,680,750,820,910,
    1e3,1.2e3,1.5e3,1.8e3,2e3,2.2e3,2.4e3,2.7e3,3e3,3.3e3,3.6e3,3.9e3,
    4.3e3,4.7e3,5.1e3,5.6e3,6.2e3,6.8e3,7.5e3,8.2e3,9.1e3,
    10e3,12e3,15e3,18e3,20e3,22e3,24e3,27e3,30e3,33e3,36e3,39e3,
    43e3,47e3,51e3,56e3,62e3,68e3,75e3,82e3,91e3,
    100e3,120e3,150e3,180e3,200e3,220e3,240e3,270e3,300e3,330e3,360e3,390e3,
    430e3,470e3,510e3,560e3,620e3,680e3,750e3,820e3,910e3,
    1e6,2e6,6.8e6,10e6
])
CAP_COMERCIALES = sorted(set([
    1e-12,2e-12,5e-12,10e-12,12e-12,15e-12,18e-12,22e-12,27e-12,30e-12,
    33e-12,39e-12,47e-12,50e-12,56e-12,68e-12,82e-12,100e-12,120e-12,
    150e-12,200e-12,330e-12,470e-12,560e-12,820e-12,
    1e-9,2e-9,3.3e-9,3.9e-9,6.9e-9,10e-9,15e-9,20e-9,33e-9,47e-9,100e-9,
    0.1e-6,0.22e-6,0.47e-6,1e-6,2.2e-6,3.3e-6,4.7e-6,
    10e-6,22e-6,33e-6,44e-6,47e-6,100e-6,220e-6,330e-6,
    470e-6,680e-6,1000e-6
]))

_RES_ADD     = [r for r in RES_COMERCIALES if R_min_add <= r <= R_max_add]
_RES_BP      = [r for r in RES_COMERCIALES if R_min_bp  <= r <= R_max_bp]
_RES_ADD_ARR = np.array(_RES_ADD, dtype=float)
_RES_ARR_ALL = np.array(RES_COMERCIALES, dtype=float)
_CAP_ARR     = np.array(CAP_COMERCIALES, dtype=float)

def fmt_cap(c):
    if c >= 1e-6: return f"{c*1e6:.3g}uF"
    if c >= 1e-9: return f"{c*1e9:.3g}nF"
    return f"{c*1e12:.3g}pF"

def fmt_res(r):
    if r >= 1e6: return f"{r/1e6:.3g}Mohm"
    if r >= 1e3: return f"{r/1e3:.3g}kohm"
    return f"{r:.3g}ohm"

def res_comercial(v):
    return float(RES_COMERCIALES[np.argmin(np.abs(_RES_ARR_ALL - v))])

def cap_comercial(v):
    return float(CAP_COMERCIALES[np.argmin(np.abs(_CAP_ARR - v))])

def res_n_vecinos(v, n):
    idx = np.argsort(np.abs(_RES_ARR_ALL - v))[:n]
    return [float(RES_COMERCIALES[i]) for i in sorted(idx)]

def cap_n_vecinos(v, n):
    idx = np.argsort(np.abs(_CAP_ARR - v))[:n]
    return [float(CAP_COMERCIALES[i]) for i in sorted(idx)]

# ════════════════════════════════════════════════════════════════════════════
# T-REDES R2
# ════════════════════════════════════════════════════════════════════════════
_Ra_max = max(_RES_BP); _Rc_min = min(_RES_BP)
R2_max_tred = 2*_Ra_max + _Ra_max**2/_Rc_min

print(f"  Precomputando T-redes R2 ({len(_RES_BP)}³ entradas)...", flush=True)
_t0 = time.time()
_tred_entries = sorted(
    [(_Ra+_Rb+_Ra*_Rb/_Rc, _Ra, _Rb, _Rc)
     for _Ra in _RES_BP for _Rb in _RES_BP for _Rc in _RES_BP],
    key=lambda x: x[0]
)
_TRED_R2_ARR = np.array([e[0] for e in _tred_entries])
print(f"  T-redes R2 listas ({len(_tred_entries)} entradas, {time.time()-_t0:.1f}s)", flush=True)

def mejores_tred(R2_target, top_n=5):
    idx0 = int(np.searchsorted(_TRED_R2_ARR, R2_target))
    lo   = max(0, idx0-300); hi = min(len(_tred_entries), idx0+300)
    seen, out = set(), []
    for R2_eq, Ra, Rb, Rc in sorted(_tred_entries[lo:hi], key=lambda e: abs(e[0]-R2_target)):
        norm = (min(Ra,Rb), max(Ra,Rb), Rc)
        if norm not in seen:
            seen.add(norm)
            out.append(dict(Ra=Ra, Rb=Rb, Rc=Rc, R2_eq=R2_eq,
                            err=abs(R2_eq-R2_target)/R2_target*100))
        if len(out) >= top_n: break
    return out

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE TRANSFERENCIA
# ════════════════════════════════════════════════════════════════════════════

def H_real_arr(R1, R2, C1, C2, freqs_hz):
    s      = 1j*2*np.pi*freqs_hz
    beta   = (s*R2*C1)/((s*R1*C1+1)*(s*R2*C2+1))
    z_load = (R2/Rin_bp_amp)/(s*R2*C2+1)
    nif    = (1+s/omega_p_bp)/A0_bp
    return -beta/(1+nif*(1+beta+z_load))

def H_ideal_arr(freqs_hz):
    s = 1j*2*np.pi*freqs_hz
    return (s**2+2*ZETA_GEO*omega0*s+omega0**2)/(s**2+2*ZETA_TARGET*omega0*s+omega0**2)

def circuito_params(R1, R2, C1, C2):
    R2_eff = R2*Rin_bp_amp/(R2+Rin_bp_amp)
    tau1   = R1*C1; tau2 = R2_eff*C2
    if tau1 <= 0 or tau2 <= 0: return None, None
    return 1/(2*np.pi*np.sqrt(tau1*tau2)), (tau1+tau2)/(2*np.sqrt(tau1*tau2))

# Constantes precomputadas (deben estar antes de las funciones que las usan)
_H_IDEAL_EVAL = H_ideal_arr(FREQS_EVAL)
_S_EVAL       = 1j*2*np.pi*FREQS_EVAL
_NIF_ADD_EVAL = (1+_S_EVAL/omega_p_add)/A0_add
_LOG_MAG_ID   = 20*np.log10(np.abs(_H_IDEAL_EVAL))

# ════════════════════════════════════════════════════════════════════════════
# ADDER VECTORIZADO
# ════════════════════════════════════════════════════════════════════════════

def _ratios_opt(H_bp):
    X    = np.column_stack([np.ones(len(FREQS_EVAL)), H_bp])
    coef, _, _, _ = np.linalg.lstsq(X, _H_IDEAL_EVAL, rcond=None)
    return float(coef[0].real), float(coef[1].real)

def buscar_adder(H_bp, top_n=5):
    a_opt, b_opt = _ratios_opt(H_bp)
    if a_opt <= 0 or b_opt <= 0: return []
    Ru_id = _RES_ADD_ARR/a_opt; Rbp_id = _RES_ADD_ARR/b_opt
    mask  = ((Ru_id >= R_min_add) & (Ru_id <= R_max_add) &
             (Rbp_id >= R_min_add) & (Rbp_id <= R_max_add))
    Rf_v  = _RES_ADD_ARR[mask]
    if len(Rf_v) == 0: return []
    Ru_v  = np.array([res_comercial(v) for v in Rf_v/a_opt])
    Rbp_v = np.array([res_comercial(v) for v in Rf_v/b_opt])
    a_v   = (Rf_v/Ru_v)[:,None]; b_v = (Rf_v/Rbp_v)[:,None]
    Rf_2d = Rf_v[:,None];        nif = _NIF_ADD_EVAL[None,:]
    Av_u  = -a_v/(1+nif*(1+a_v+Rf_2d/Rin_add))
    Av_bp = -b_v/(1+nif*(1+b_v+Rf_2d/Rin_add))
    H_tot = Av_u + Av_bp*H_bp[None,:]
    mag_t = np.abs(H_tot)
    with np.errstate(divide='ignore', invalid='ignore'):
        log_t = np.where(mag_t > 1e-30, 20*np.log10(mag_t), np.nan)
    diff    = _LOG_MAG_ID[None,:] - log_t
    K_dB    = np.nanmean(diff, axis=1)
    err_rms = np.sqrt(np.nanmean((diff-K_dB[:,None])**2, axis=1))
    seen, out = set(), []
    for i in np.argsort(err_rms):
        key = (int(Ru_v[i]), int(Rbp_v[i]))
        if key in seen: continue
        seen.add(key)
        out.append(dict(Ru=float(Ru_v[i]), Rbp=float(Rbp_v[i]), Rf=float(Rf_v[i]),
                        Rf_type='S', a_real=float(Rf_v[i]/Ru_v[i]),
                        b_real=float(Rf_v[i]/Rbp_v[i]),
                        err_rms=float(err_rms[i]), K_opt_dB=float(K_dB[i])))
        if len(out) >= top_n: break
    return out

# ════════════════════════════════════════════════════════════════════════════
# FUNCIÓN OBJETIVO CONTINUA  (debe ser picklable → definida a nivel módulo)
# Variables: x = [log10(R1), log10(C1), log10(C2)]
# R2 determinado por ω0 exacto.  Adder: ratios óptimos sin discretizar.
# ════════════════════════════════════════════════════════════════════════════

def _r2_from_f0(R1, C1, C2):
    tau1 = R1*C1
    return 1.0/(omega0**2*tau1*C2) if tau1 > 0 else None

def error_continuo(x, zeta_target=None, zeta_pen_w=50.0):
    R1 = 10**x[0]; C1 = 10**x[1]; C2 = 10**x[2]
    R2 = _r2_from_f0(R1, C1, C2)
    # Hard constraint: R2 must be realizable with commercial resistors / T-redes
    if R2 is None or R2 < R_min_bp or R2 > R2_max_tred: return 1e6

    R2_eff = R2*Rin_bp_amp/(R2+Rin_bp_amp)
    tau1   = R1*C1; tau2 = R2_eff*C2
    if tau1 <= 0 or tau2 <= 0: return 1e6
    zeta = (tau1+tau2)/(2*np.sqrt(tau1*tau2))

    zeta_pen = 0.0
    if zeta < ZETA_MIN:
        zeta_pen += ((ZETA_MIN-zeta)/ZETA_MIN)**2
    elif zeta > ZETA_MAX:
        zeta_pen += ((zeta-ZETA_MAX)/ZETA_MAX)**2
    if zeta_target is not None:
        zeta_pen += ((zeta-zeta_target)/zeta_target)**2

    H_bp = H_real_arr(R1, R2, C1, C2, FREQS_EVAL)
    X    = np.column_stack([np.ones(len(FREQS_EVAL)), H_bp])
    coef, _, _, _ = np.linalg.lstsq(X, _H_IDEAL_EVAL, rcond=None)
    a_opt = coef[0].real; b_opt = coef[1].real
    if a_opt <= 0 or b_opt <= 0: return 1e3 + zeta_pen_w*zeta_pen

    H_approx = -(a_opt + b_opt*H_bp)
    mag_app  = np.abs(H_approx)
    mask     = mag_app > 1e-30
    if not np.any(mask): return 1e3 + zeta_pen_w*zeta_pen
    diff = _LOG_MAG_ID[mask] - 20*np.log10(mag_app[mask])
    K_dB = np.mean(diff)
    err  = float(np.sqrt(np.mean((diff-K_dB)**2)))
    return err + zeta_pen_w*zeta_pen


# Función top-level picklable para ProcessPoolExecutor (Fase 2)
_BOUNDS_LOG = [
    (np.log10(R_min_bp),            np.log10(R_max_bp)),
    (np.log10(min(CAP_COMERCIALES)), np.log10(max(CAP_COMERCIALES))),
    (np.log10(min(CAP_COMERCIALES)/2), np.log10(max(CAP_COMERCIALES)*2)),
]

def _optim_banda(args):
    """Optimización multi-start para una banda de ζ.  Ejecuta en proceso separado."""
    z_tgt, seed, n_restarts, n_iter = args
    rng  = np.random.default_rng(seed)
    best = None
    obj  = partial(error_continuo, zeta_target=z_tgt, zeta_pen_w=80.0)
    for _ in range(n_restarts):
        x0 = np.array([rng.uniform(*b) for b in _BOUNDS_LOG])
        try:
            res = minimize(obj, x0, method='L-BFGS-B', bounds=_BOUNDS_LOG,
                           options={'maxiter': n_iter, 'ftol': 1e-10, 'gtol': 1e-8})
            if best is None or res.fun < best.fun:
                best = res
        except Exception:
            pass
    return best


# Función top-level picklable para snap + adder (Fase 3)
def _snap_combo(args):
    R1_c, C1_c, C2_val, C2_type, C2_a, C2_b = args
    R2_ideal = _r2_from_f0(R1_c, C1_c, C2_val)
    if R2_ideal is None: return None
    if R2_ideal < R_min_bp*0.5 or R2_ideal > R2_max_tred*2: return None

    R2_c       = res_comercial(R2_ideal)
    err_single = abs(R2_c-R2_ideal)/R2_ideal*100
    tred_list  = mejores_tred(R2_ideal, top_n=5)
    tred_best  = tred_list[0] if tred_list else None
    err_tred   = tred_best['err'] if tred_best else np.inf

    if tred_best and err_tred < err_single:
        R2_act = tred_best['R2_eq']; r2_type = 'T'; r2_err = err_tred
    else:
        R2_act = R2_c;               r2_type = 'S'; r2_err = err_single

    f0v, zv = circuito_params(R1_c, R2_act, C1_c, C2_val)
    if f0v is None: return None
    if abs(f0v-f0_des)/f0_des*100 > F0_TOL_PCT: return None
    if not (ZETA_MIN <= zv <= ZETA_MAX): return None

    H_bp       = H_real_arr(R1_c, R2_act, C1_c, C2_val, FREQS_EVAL)
    adder_list = buscar_adder(H_bp, top_n=5)
    if not adder_list: return None
    ba = adder_list[0]
    return dict(R1=R1_c, C1=C1_c, C2=C2_val,
                C2_type=C2_type, C2_a=C2_a, C2_b=C2_b,
                R2_ideal=R2_ideal, R2_act=R2_act,
                r2_type=r2_type, r2_err=r2_err, tred_list=tred_list,
                f0=f0v, f0_err=abs(f0v-f0_des)/f0_des*100, zeta=zv,
                err_rms=ba['err_rms'], K_opt_dB=ba['K_opt_dB'],
                K_opt_lin=10**(ba['K_opt_dB']/20),
                adder_list=adder_list, best_adder=ba,
                Rref=res_comercial(R1_c*R2_act/(R1_c+R2_act)))


def vecindad_comercial(x_opt):
    # Solo R1 en el rango del BP
    R1s = [r for r in res_n_vecinos(10**x_opt[0], N_SNAP_NEIGHBORS)
           if R_min_bp <= r <= R_max_bp]
    if not R1s: R1s = [res_comercial(np.clip(10**x_opt[0], R_min_bp, R_max_bp))]

    C1s = cap_n_vecinos(10**x_opt[1], N_SNAP_NEIGHBORS)

    # C2: por cada (R1_c, C1_c) agregar TODOS los caps que dan R2 en [R_min_bp, R2_max_tred].
    # Así el snap SIEMPRE encuentra candidatos realizables, aunque el óptimo
    # continuo haya terminado con R2 fuera de rango.
    seen_c2  = set()
    c2_cands = []

    def _add_c2(c, typ, ca=None, cb=None):
        if c not in seen_c2:
            seen_c2.add(c); c2_cands.append((c, typ, ca, cb))

    # 1) vecinos del óptimo continuo (contexto original)
    for c in cap_n_vecinos(10**x_opt[2], N_SNAP_NEIGHBORS):
        _add_c2(c, 'S')

    # 2) para cada (R1,C1) comercial, agregar caps que dan R2 en rango
    for R1_c in R1s:
        for C1_c in C1s:
            tau1 = R1_c * C1_c
            if tau1 <= 0: continue
            # R2 = 1/(ω0²·τ1·C2) → C2 = 1/(ω0²·τ1·R2)
            C2_min = 1.0 / (omega0**2 * tau1 * R2_max_tred)   # R2=max → C2=min
            C2_max = 1.0 / (omega0**2 * tau1 * R_min_bp)      # R2=min → C2=max
            for c in CAP_COMERCIALES:
                if C2_min <= c <= C2_max:
                    _add_c2(c, 'S')
            # paralelo de dos caps dentro del rango
            in_range = [c for c in CAP_COMERCIALES if C2_min/2 <= c <= C2_max]
            for ca in in_range:
                for cb in in_range:
                    if ca <= cb:
                        cp = ca+cb
                        if C2_min <= cp <= C2_max: _add_c2(cp, 'P', ca, cb)
                        cs = ca*cb/(ca+cb)
                        if C2_min <= cs <= C2_max: _add_c2(cs, 'E', ca, cb)

    return [(R1_c, C1_c, C2_val, C2_type, C2_a, C2_b)
            for R1_c in R1s for C1_c in C1s
            for C2_val, C2_type, C2_a, C2_b in c2_cands]


# ════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN (dentro de __main__ para compatibilidad con multiprocessing)
# ════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':

    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"\n  CPUs: {N_WORKERS}  |  RAM límite: {RAM_LIMIT_GB}GB  "
          f"→ max {MAX_PROC_WORKERS} procesos / {SNAP_CHUNK_SIZE} combos/chunk", flush=True)

    # ── Fase 1: DE global ────────────────────────────────────────────────────
    print(f"\n═══ FASE 1: differential_evolution  "
          f"({N_GEN_DE} gen × pop {POPSIZE_DE} × {len(_BOUNDS_LOG)} vars, "
          f"workers={N_WORKERS}) ═══", flush=True)
    _t_de    = time.time()
    _de_best = [np.inf]
    pbar_de  = tqdm(total=N_GEN_DE, desc="DE global", unit="gen", ncols=110)

    def _de_cb(xk, convergence):
        try:
            err = error_continuo(xk)
            if err < _de_best[0]: _de_best[0] = err
        except Exception:
            pass
        pbar_de.set_postfix_str(f"best={_de_best[0]:.4f}dB  conv={convergence:.4f}")
        pbar_de.update(1)
        return False

    result_de = differential_evolution(
        error_continuo, _BOUNDS_LOG,
        maxiter=N_GEN_DE, popsize=POPSIZE_DE, tol=1e-8,
        seed=42, polish=True, callback=_de_cb,
        workers=MAX_PROC_WORKERS,   # ← paralelismo con límite de RAM
        mutation=(0.5, 1.5), recombination=0.9,
        updating='deferred'         # necesario con workers > 1
    )
    pbar_de.close()

    # Mostrar óptimo global continuo
    _R1g = 10**result_de.x[0]; _C1g = 10**result_de.x[1]; _C2g = 10**result_de.x[2]
    _R2g = _r2_from_f0(_R1g, _C1g, _C2g) or 0
    _R2g_eff = _R2g*Rin_bp_amp/(_R2g+Rin_bp_amp) if _R2g else 0
    _t1g = _R1g*_C1g; _t2g = _R2g_eff*_C2g
    _zg  = (_t1g+_t2g)/(2*np.sqrt(_t1g*_t2g)) if _t1g*_t2g > 0 else 0
    print(f"  Completado en {time.time()-_t_de:.1f}s  "
          f"err_continuo={result_de.fun:.4f}dB  ζ≈{_zg:.1f}", flush=True)
    print(f"  Óptimo: R1={fmt_res(_R1g)}  C1={fmt_cap(_C1g)}  "
          f"C2={fmt_cap(_C2g)}  R2≈{fmt_res(_R2g)}", flush=True)

    # ── Fase 2: optimización por banda de ζ (paralelizada) ──────────────────
    _ZETA_MIDS = [150, 250, 350, 450, 550, 650, 750, 850, 950]
    print(f"\n═══ FASE 2: {len(_ZETA_MIDS)} bandas × {N_RESTARTS_BAND} restarts "
          f"en {min(MAX_PROC_WORKERS, len(_ZETA_MIDS))} procesos ═══", flush=True)

    band_args = [
        (z_tgt, 42 + i*17, N_RESTARTS_BAND, N_ITER_LOCAL)
        for i, z_tgt in enumerate(_ZETA_MIDS)
    ]

    continuous_solutions = [result_de]
    _t_banda = time.time()
    with ProcessPoolExecutor(max_workers=min(MAX_PROC_WORKERS, len(_ZETA_MIDS))) as ex:
        futures = {ex.submit(_optim_banda, a): a for a in band_args}
        with tqdm(total=len(band_args), desc="Bandas ζ", unit="banda", ncols=90) as pbar:
            for fut in as_completed(futures):
                res = fut.result()
                if res is not None:
                    continuous_solutions.append(res)
                z_done = futures[fut][0]
                pbar.set_postfix_str(f"ζ_banda={z_done:.0f}  sols={len(continuous_solutions)}")
                pbar.update(1)

    print(f"  Fase 2 completada en {time.time()-_t_banda:.1f}s  "
          f"→ {len(continuous_solutions)} soluciones continuas", flush=True)

    # ── Fase 3: snap a comerciales — chunked para limitar RAM ───────────────
    print(f"\n═══ FASE 3: snap a comerciales  "
          f"(chunks={SNAP_CHUNK_SIZE}, threads={min(N_WORKERS*2,32)}) ═══",
          flush=True)
    _t_snap = time.time()

    # Generar todas las combinaciones a evaluar (deduplicadas)
    all_combos = []
    seen_combo = set()
    for sol in continuous_solutions:
        for combo in vecindad_comercial(sol.x):
            key = (combo[0], combo[1], round(combo[2], 30))
            if key not in seen_combo:
                seen_combo.add(key)
                all_combos.append(combo)
    print(f"  {len(all_combos)} combinaciones únicas a evaluar.", flush=True)

    # Archivo temporal para resultados parciales (evita acumular en RAM)
    import pickle, tempfile
    _tmp_file = os.path.join(script_dir, "_snap_tmp.pkl")
    n_threads  = min(N_WORKERS*2, 32)
    n_validos  = 0

    with open(_tmp_file, 'wb') as _tf:
        chunks = [all_combos[i:i+SNAP_CHUNK_SIZE]
                  for i in range(0, len(all_combos), SNAP_CHUNK_SIZE)]
        with tqdm(total=len(all_combos), desc="Snap+Adder",
                  unit="combo", ncols=90) as pbar:
            for chunk in chunks:
                with ThreadPoolExecutor(max_workers=n_threads) as ex:
                    futs = {ex.submit(_snap_combo, c): c for c in chunk}
                    for fut in as_completed(futs):
                        r = fut.result()
                        if r is not None:
                            pickle.dump(r, _tf)
                            n_validos += 1
                        pbar.set_postfix_str(f"válidos={n_validos}")
                        pbar.update(1)

    # Leer resultados del archivo temporal
    verified = []
    with open(_tmp_file, 'rb') as _tf:
        while True:
            try: verified.append(pickle.load(_tf))
            except EOFError: break
    os.remove(_tmp_file)

    print(f"  Fase 3 completada en {time.time()-_t_snap:.1f}s  "
          f"→ {len(verified)} candidatos válidos", flush=True)

    if not verified:
        print("  Sin candidatos válidos. Probá ampliar rangos o N_SNAP_NEIGHBORS.", flush=True)
        raise SystemExit(0)

    verified.sort(key=lambda x: (x['err_rms'], x['f0_err']))

    # Seleccionar top con diversidad de ζ
    _ZETA_BANDS = [(100,200),(200,300),(300,400),(400,500),
                   (500,600),(600,700),(700,800),(800,900),(900,1001)]

    def _cand_key(v):
        return (v['R1'], v['C1'], round(v['C2'],30), round(v['R2_act'],1))

    results = []; seen_cand = set()
    for lo, hi in _ZETA_BANDS:
        for v in verified:
            if lo <= v['zeta'] < hi and _cand_key(v) not in seen_cand:
                results.append(v); seen_cand.add(_cand_key(v)); break
    for v in verified:
        if len(results) >= TOP_N: break
        if _cand_key(v) not in seen_cand:
            results.append(v); seen_cand.add(_cand_key(v))
    results.sort(key=lambda x: x['zeta'])

    # ════════════════════════════════════════════════════════════════════════
    # OUTPUT
    # ════════════════════════════════════════════════════════════════════════
    import matplotlib.pyplot as plt

    SEP = "="*150; sep = "-"*150

    print(); print(SEP)
    print("  COMPENSADOR ÓPTIMO — differential_evolution + paralelismo + snap")
    print(f"  H_ideal=(s²+2ζ₀ω₀s+ω₀²)/(s²+2ζ₁ω₀s+ω₀²)  "
          f"ζ₀={ZETA_GEO}  ζ₁={ZETA_TARGET}  f0={f0_des}Hz")
    print(f"  workers={N_WORKERS}  DE:{N_GEN_DE}gen×pop{POPSIZE_DE}  "
          f"bandas:{len(_ZETA_MIDS)}×{N_RESTARTS_BAND}restarts")
    print(SEP)
    hdr = (f"{'#':<3} {'C1':>8} {'C2':>10} {'R1':>10} {'R2_act':>11}"
           f"  {'f0':>7} {'ζ':>8} {'f0err%':>7}"
           f"  {'errRMS_dB':>10} {'K_opt_dB':>9}"
           f"  {'Ru':>10} {'Rbp':>10} {'Rf':>10}"
           f"  {'a=Rf/Ru':>8} {'b=Rf/Rbp':>9}")
    print(hdr); print(sep)

    _C2_TAG = {'S': ' ', 'P': 'P', 'E': 'E'}
    for i, r in enumerate(results):
        ba  = r['best_adder']
        c2t = _C2_TAG.get(r['C2_type'], ' ')
        print(f"{i+1:<3} {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>9}{c2t}"
              f" {fmt_res(r['R1']):>10} {fmt_res(r['R2_act']):>11}"
              f"  {r['f0']:>7.3f} {r['zeta']:>8.2f} {r['f0_err']:>6.2f}%"
              f"  {r['err_rms']:>9.4f}dB {r['K_opt_dB']:>+9.2f}dB"
              f"  {fmt_res(ba['Ru']):>10} {fmt_res(ba['Rbp']):>10} {fmt_res(ba['Rf']):>10}"
              f"  {ba['a_real']:>8.4f} {ba['b_real']:>9.4f}")

    print(f"\n  (P=C2 paralelo  E=C2 serie  R2?=T→T-red)")

    # ── CSV ────────────────────────────────────────────────────────────────────
    csv_path = os.path.join(script_dir, "compensador_optimo.csv")
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        header = ["rank","C1_F","C2_F","C2_type","C2_a_F","C2_b_F",
                  "R1_ohm","R2_ideal_ohm","R2_act_ohm","Rref_ohm",
                  "f0_Hz","f0_err_pct","zeta","err_rms_dB",
                  "K_opt_dB","K_opt_lin","R2_type","R2_err_pct",
                  "Ru_ohm","Rbp_ohm","Rf_ohm","a_real","b_real"]
        for j in range(1,6):
            header += [f"T{j}_Ra",f"T{j}_Rb",f"T{j}_Rc",f"T{j}_R2eq",f"T{j}_err"]
        for j in range(1,6):
            header += [f"A{j}_Ru",f"A{j}_Rbp",f"A{j}_Rf",f"A{j}_err",f"A{j}_K"]
        w.writerow(header)
        for i, r in enumerate(results):
            ba  = r['best_adder']
            row = [i+1, r['C1'], r['C2'], r['C2_type'],
                   r['C2_a'] or '', r['C2_b'] or '',
                   r['R1'], r['R2_ideal'], r['R2_act'], r['Rref'],
                   r['f0'], round(r['f0_err'],4), round(r['zeta'],4),
                   round(r['err_rms'],6), round(r['K_opt_dB'],4),
                   round(r['K_opt_lin'],6), r['r2_type'], round(r['r2_err'],4),
                   ba['Ru'], ba['Rbp'], ba['Rf'],
                   round(ba['a_real'],6), round(ba['b_real'],6)]
            for j in range(5):
                t = r['tred_list'][j] if j < len(r['tred_list']) else {}
                row += [t.get('Ra',''), t.get('Rb',''), t.get('Rc',''),
                        round(t['R2_eq'],2) if 'R2_eq' in t else '',
                        round(t['err'],4)   if 'err'  in t else '']
            for j in range(5):
                ad = r['adder_list'][j] if j < len(r['adder_list']) else {}
                row += [ad.get('Ru',''), ad.get('Rbp',''), ad.get('Rf',''),
                        round(ad['err_rms'],6)  if 'err_rms'  in ad else '',
                        round(ad['K_opt_dB'],4) if 'K_opt_dB' in ad else '']
            w.writerow(row)
    print(f"\n  CSV guardado: {csv_path}", flush=True)

    # ── Gráfica ─────────────────────────────────────────────────────────────────
    freqs_plot = np.logspace(-3, 6, 3000)
    H_id_plt   = H_ideal_arr(freqs_plot)
    mag_id     = 20*np.log10(np.abs(H_id_plt))
    colores    = ['royalblue','forestgreen','firebrick','darkorange',
                  'purple','teal','brown','crimson']
    N_PLOT     = min(5, len(results))

    fig, (ax_mag, ax_err) = plt.subplots(2,1, figsize=(15,10), sharex=True)
    ax_mag.semilogx(freqs_plot, mag_id, 'k--', lw=2.5, label='H_ideal (ref)')
    for i in range(N_PLOT):
        r   = results[i]; col = colores[i%len(colores)]; ba = r['best_adder']
        s   = 1j*2*np.pi*freqs_plot
        nif = (1+s/omega_p_add)/A0_add
        H_t = H_real_arr(r['R1'], r['R2_act'], r['C1'], r['C2'], freqs_plot)
        a   = ba['Rf']/ba['Ru']; b = ba['Rf']/ba['Rbp']; Rf = ba['Rf']
        H_tot = (-a/(1+nif*(1+a+Rf/Rin_add))) + (-b/(1+nif*(1+b+Rf/Rin_add)))*H_t
        mag_K = 20*np.log10(np.abs(H_tot)) + r['K_opt_dB']
        lbl   = (f"#{i+1} ζ={r['zeta']:.0f} R1={fmt_res(r['R1'])} "
                 f"C1={fmt_cap(r['C1'])} C2={fmt_cap(r['C2'])} "
                 f"err={r['err_rms']:.2f}dB")
        ax_mag.semilogx(freqs_plot, mag_K, color=col, lw=1.8, label=lbl)
        ax_err.semilogx(freqs_plot, mag_K-mag_id, color=col, lw=1.5,
                        label=f"#{i+1} ζ={r['zeta']:.0f}")

    ax_mag.axvline(f0_des, color='gray', ls=':', lw=1)
    ax_mag.set_ylabel('Magnitud (dB)'); ax_mag.legend(loc='lower right', fontsize=6)
    ax_mag.set_title('Compensador Óptimo (DE + paralelismo)\nK·|H_total| vs H_ideal')
    ax_mag.grid(True, which='both', ls=':', alpha=0.4)
    ax_mag.set_xlim([freqs_plot[0], freqs_plot[-1]])
    ax_err.axhline(0, color='k', lw=0.8); ax_err.axvline(f0_des, color='gray', ls=':', lw=1)
    ax_err.set_xlabel('Frecuencia (Hz)'); ax_err.set_ylabel('Error (dB)')
    ax_err.legend(fontsize=8); ax_err.grid(True, which='both', ls=':', alpha=0.4)
    plt.tight_layout()
    plt.savefig(os.path.join(script_dir, "compensador_optimo.png"), dpi=150)
    plt.show()
