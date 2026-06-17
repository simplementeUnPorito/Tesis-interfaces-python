# -*- coding: utf-8 -*-
"""
calculoCompensadorOptimo.py
Búsqueda inteligente usando scipy differential_evolution + paralelismo.

Espacio continuo 3-D: [log10(R1), log10(C1), log10(C2)]
  → R2 determinado por condición ω0 exacta (resistor único comercial)
  → Ratios adder optimizados analíticamente (lstsq), reescalados (a,b)→(ka,kb)
    si max(a,b) > ADDER_GAIN_MAX, sin tocar errRMS (invariante a escala)
  → Cf del adder calculado para fc=1/(2πRfCf)≈CF_TARGET_FC
  → Snap a comerciales al final

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
from scipy.optimize import differential_evolution, minimize, minimize_scalar

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
M_GEOPHONE  = 0.011                          # kg
K_GEOPHONE  = M_GEOPHONE * omega0**2         # N/m
D_GEOPHONE  = 2*M_GEOPHONE*ZETA_GEO*omega0   # Ns/m
# H_geo(s) = N_geophone/D_geophone = wn·s / (s² + 2·hi·wn·s + wn²)

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
ZETA_MIN    = 800
ZETA_MAX    = 1000
TOP_N       = 15

# ── Ganancia pico del filtro BP (techo y piso) ────────────────────────────────
# BP inversor: gpeak = R2·C1 / (R1·C1 + R2·C2).
# GAIN_BP_MAX: evita saturación y que el adder atenúe lo que el BP ya
# amplificó (desperdicio de rango dinámico).
# GAIN_BP_MIN: evita que el BP atenúe la señal por debajo de 0.5 — eso
# entierra la señal cerca del piso de ruido en vez de aportar información.
# CRÍTICO: no relajar ninguno de los dos.
GAIN_BP_MAX = 1.5
GAIN_BP_MIN = 0.5

# ── Restricciones del adder (Ru, Rbp, Rf) ────────────────────────────────────
# a = Rf/Ru, b = Rf/Rbp — ganancia de CADA rama del adder (no la suma).
# Heurística: ninguna etapa SOLA (BP, o cada rama del adder) debe superar
# ganancia 5 — la cascada combinada (BP→adder, o adder solo) puede llegar a
# ganancias totales enormes (cientos/miles) sin problema, lo que se evita es
# una ÚNICA etapa con ganancia excesiva (ruido/saturación/ancho de banda).
# (Nota: NO se exige Ru<=Rbp — esa restricción es incompatible con el ajuste
# óptimo del compensador, ya que forzar a>=b destruye la cancelación que
# genera el notch en f0 y dispara errRMS a >15dB.  En cambio, capear el MAYOR
# de (a,b) a ADDER_GAIN_MAX es "gratis": err_rms es invariante a escalar
# (a,b) por una constante, así que siempre se puede achicar sin tocar el
# error si alguno de los dos supera el límite.)
ADDER_GAIN_MAX = 5.0

# ── Polo de filtrado HF en el feedback del adder (Rf || Cf) ──────────────────
CF_TARGET_FC = 435.713      # Hz
OMEGA_CF     = 2*np.pi*CF_TARGET_FC

# ── PGA entre el geófono y el resto de la cadena ─────────────────────────────
PGA_MIN = 2.0
PGA_MAX = 100.0

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
N_SNAP_NEIGHBORS   = 8      # vecinos comerciales ±N alrededor del óptimo

# ── Frecuencias ───────────────────────────────────────────────────────────────
# Límite superior por debajo del polo Cf (CF_TARGET_FC): el rolloff del adder
# por encima de la banda de interés es deliberado (filtrado HF), no se evalúa
# como error contra H_ideal (que vale ~1 en alta frecuencia).
F_EVAL_MAX = 500.0
FREQS_EVAL = np.logspace(np.log10(f0_des/1000), np.log10(F_EVAL_MAX), 800)

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

def fmt_hz(f):
    if f >= 1e3: return f"{f/1e3:.3g}kHz"
    return f"{f:.3g}Hz"

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

def cap_best_parallel(target, n_near=12):
    """Mejor aproximación a `target`: un comercial solo, o la suma paralela
    de dos comerciales (cerámicos chicos, fáciles de combinar en la placa).
    Devuelve (valor_total, tipo, ca, cb) con tipo 'S' (solo) o 'P' (paralelo)."""
    c_single = cap_comercial(target)
    best = (c_single, 'S', None, None, abs(c_single-target))
    for ca in cap_n_vecinos(target, n_near):
        cb_ideal = target - ca
        if cb_ideal <= 0: continue
        cb = cap_comercial(cb_ideal)
        total = ca + cb
        err = abs(total-target)
        if err < best[4]:
            best = (total, 'P', ca, cb, err)
    return best[0], best[1], best[2], best[3]

# ════════════════════════════════════════════════════════════════════════════
# R2: resistor único comercial (sin T-red — las ganancias BP ahora son chicas
# y la T-red ya no es necesaria para alcanzar la precisión requerida)
# ════════════════════════════════════════════════════════════════════════════
R2_max_single = max(_RES_BP)

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE TRANSFERENCIA
# ════════════════════════════════════════════════════════════════════════════

def H_real_arr(R1, R2, C1, C2, freqs_hz):
    s      = 1j*2*np.pi*freqs_hz
    beta   = (s*R2*C1)/((s*R1*C1+1)*(s*R2*C2+1))
    z_load = (R2/Rin_bp_amp)/(s*R2*C2+1)
    nif    = (1+s/omega_p_bp)/A0_bp
    return -beta/(1+nif*(1+beta+z_load))

def H_notch_arr(freqs_hz):
    s = 1j*2*np.pi*freqs_hz
    return (s**2+2*ZETA_GEO*omega0*s+omega0**2)/(s**2+2*ZETA_TARGET*omega0*s+omega0**2)

def H_ideal_arr(freqs_hz):
    s = 1j*2*np.pi*freqs_hz
    H_lp = 1.0/(1+s/OMEGA_CF)             # cascada con el LP real Rf||Cf del adder
    return H_notch_arr(freqs_hz) * H_lp

def H_geo_arr(freqs_hz, zeta):
    s = 1j*2*np.pi*freqs_hz
    return omega0*s/(s**2+2*zeta*omega0*s+omega0**2)

def gain_passband_full(r):
    """|K · H_geo(f0_des) · H_total(f0_des)| de la cascada geófono+compensador,
    evaluada en f0_des (dentro de la banda plana, entre el polo bajo del notch
    y el polo alto del adder Rf||Cf)."""
    ba  = r['best_adder']
    s   = 1j*2*np.pi*f0_des
    nif = (1+s/omega_p_add)/A0_add
    H_t = H_real_arr(r['R1'], r['R2_act'], r['C1'], r['C2'], f0_des)
    Rf, Cf_act = ba['Rf'], ba['Cf_act']
    Zf  = Rf/(1+s*Rf*Cf_act)
    a   = Zf/ba['Ru']; b = Zf/ba['Rbp']
    H_tot = (-a/(1+nif*(1+a+Zf/Rin_add))) + (-b/(1+nif*(1+b+Zf/Rin_add)))*H_t
    H_geo_f0 = H_geo_arr(f0_des, ZETA_GEO)
    K_lin = 10**(r['K_opt_dB']/20)
    return float(np.abs(H_geo_f0 * H_tot * K_lin))

def H_comp_arr(r, freqs_hz, rbp_override=None):
    """H_total (BP+adder) del compensador.  rbp_override permite evaluar un
    Rbp distinto al comercial ya elegido (Ru/Rf quedan fijos)."""
    ba  = r['best_adder']
    rbp = rbp_override if rbp_override is not None else ba['Rbp']
    s   = 1j*2*np.pi*freqs_hz
    nif = (1+s/omega_p_add)/A0_add
    H_t = H_real_arr(r['R1'], r['R2_act'], r['C1'], r['C2'], freqs_hz)
    Rf, Cf_act = ba['Rf'], ba['Cf_act']
    Zf  = Rf/(1+s*Rf*Cf_act)
    a   = Zf/ba['Ru']; b = Zf/rbp
    return (-a/(1+nif*(1+a+Zf/Rin_add))) + (-b/(1+nif*(1+b+Zf/Rin_add)))*H_t

GAIN_CHECK_FREQ = 100.0   # Hz, representativo de la banda de paso (~1-500Hz)

def stage_gains_max(r):
    """Ganancias de cada etapa evaluadas en GAIN_CHECK_FREQ (frecuencia
    media de la banda de paso, no un sweep+max) — un sweep completo puede
    caer justo en el notch (donde BP y Adder se cancelan a propósito, dando
    un 'total' artificialmente chico) o en los extremos de rolloff, que no
    representan el régimen normal de uso.  Todo SIN escalar por K (lo que
    realmente ve cada op-amp en hardware):
      gpeak_bp      — BP solo (no Geo·BP) en GAIN_CHECK_FREQ
      avbp_rbp      — Adder respecto a BP: b=Rf/Rbp (resistivo, diseño)
      avu_ru        — Adder respecto a U:  a=Rf/Ru  (resistivo, diseño)
      rf_par        — Rf/(Rbp||Ru) = a+b
      max_avu       — |Av_u| real (con Cf/nif) en GAIN_CHECK_FREQ
      max_avbp      — |Av_bp| real (con Cf/nif) en GAIN_CHECK_FREQ
      max_avbp_hbp  — |Av_bp·H_bp| (aporte real de la rama BP en el adder)
      max_htot      — |H_total| = BP·Adder combinado en GAIN_CHECK_FREQ
      max_geo_htot  — |H_geo·H_total| = Geo·BP·Adder combinado en GAIN_CHECK_FREQ
    """
    ba   = r['best_adder']
    s    = 1j*2*np.pi*GAIN_CHECK_FREQ
    nif  = (1+s/omega_p_add)/A0_add
    H_bp = H_real_arr(r['R1'], r['R2_act'], r['C1'], r['C2'], GAIN_CHECK_FREQ)
    Rf, Cf_act = ba['Rf'], ba['Cf_act']
    Zf   = Rf/(1+s*Rf*Cf_act)
    a    = Zf/ba['Ru']; b = Zf/ba['Rbp']
    Av_u  = -a/(1+nif*(1+a+Zf/Rin_add))
    Av_bp = -b/(1+nif*(1+b+Zf/Rin_add))
    H_tot = Av_u + Av_bp*H_bp
    H_geo_tot = H_geo_arr(GAIN_CHECK_FREQ, ZETA_GEO) * H_tot
    Rbp_par_Ru = (ba['Rbp']*ba['Ru'])/(ba['Rbp']+ba['Ru'])   # Rbp || Ru
    rf_par     = ba['Rf']/Rbp_par_Ru                          # = a+b (exacto)
    return dict(gpeak_bp=float(np.abs(H_bp)),
                avbp_rbp=float(ba['b_real']), avu_ru=float(ba['a_real']),
                rf_par=float(rf_par),
                max_avu=float(np.abs(Av_u)),
                max_avbp=float(np.abs(Av_bp)),
                max_avbp_hbp=float(np.abs(Av_bp*H_bp)),
                max_htot=float(np.abs(H_tot)),
                max_geo_htot=float(np.abs(H_geo_tot)))

def H_geo_cascade_arr(r, freqs_hz, rbp_override=None):
    """H_geo_real(freqs) · H_total_circuito_real(freqs) para un resultado r."""
    return H_geo_arr(freqs_hz, ZETA_GEO) * H_comp_arr(r, freqs_hz, rbp_override)

def err_rms_comp_at(r, rbp_value):
    """errRMS (compensador solo) en FREQS_EVAL, con un Rbp arbitrario — misma
    métrica que ranquea los candidatos, pero con Ru/Rf ya fijos."""
    H = H_comp_arr(r, FREQS_EVAL, rbp_value)
    mag = np.abs(H)
    mask = mag > 1e-30
    if not np.any(mask): return 1e6, 0.0
    diff = _LOG_MAG_ID[mask] - 20*np.log10(mag[mask])
    K = float(np.mean(diff))
    return float(np.sqrt(np.mean((diff-K)**2))), K

def rbp_optimal(r):
    """Rbp continuo que minimiza errRMS dado que Ru y Rf YA están fijos
    (comerciales) — el verdadero 'ideal' para calibrar con R_fija+R_pot.
    (El a_opt/b_opt del ajuste conjunto lstsq asume Ru también continuo, así
    que Rf/b_opt NO es el objetivo correcto una vez que Ru ya se redondeó.)"""
    ba = r['best_adder']
    def obj(rbp):
        if rbp <= 0: return 1e6
        err, _ = err_rms_comp_at(r, rbp)
        return err
    res = minimize_scalar(obj, bounds=(ba['Rbp']/20.0, ba['Rbp']*20.0),
                           method='bounded')
    return float(res.x)

def phase_flatness(r, octaves=1.0, n=41):
    """Desviación estándar de la fase (°, desenrollada) de H_geo·H_total en una
    banda de ±`octaves` octavas alrededor de f0_des.  Menor = más plana."""
    f_lo = f0_des / (2**octaves); f_hi = f0_des * (2**octaves)
    freqs_pf = np.logspace(np.log10(f_lo), np.log10(f_hi), n)
    full  = H_geo_cascade_arr(r, freqs_pf)
    phase = np.degrees(np.unwrap(np.angle(full)))
    return float(np.std(phase))

# Potenciómetros comerciales típicos (décadas) para Rbp = R_fija + R_pot
_POT_VALUES = [1.0,10.0,100.0,1e3,10e3,100e3,1e6]

def _rbp_pot_search(rbp_ideal, pot_cands):
    best = None
    for r_pot in pot_cands:
        target_fixed = rbp_ideal - r_pot/2.0       # centra el wiper en 50%
        idx0 = int(np.searchsorted(_RES_ARR_ALL, target_fixed))
        for j in range(max(0, idx0-3), min(len(RES_COMERCIALES), idx0+3)):
            r_fixed = RES_COMERCIALES[j]
            gap = rbp_ideal - r_fixed
            if not (0.0 <= gap <= r_pot): continue
            wiper = gap/r_pot
            score = abs(wiper-0.5)
            if best is None or score < best[0]:
                best = (score, dict(R_fixed=float(r_fixed), R_pot=float(r_pot),
                                     wiper_frac=float(wiper), gap_ohm=float(gap)))
    return best[1] if best else None

def rbp_fixed_pot(rbp_ideal):
    """Descompone Rbp_ideal = R_fija(comercial, 'stack') + R_pot (serie).
    Busca, entre los R_pot disponibles (décadas, hasta ~1/3 de Rbp y no menos
    de ~1/1000 de Rbp) y varios R_fija comerciales cercanos a
    (rbp_ideal - R_pot/2), la combinación que deje el wiper MÁS CERCA DE 50%
    — un pot calibrado con una cantidad de vueltas razonable, ni al final ni
    al principio del recorrido.  Si Rbp_ideal cae casi exacto en un comercial,
    el resto a cubrir es chico y un pot 'grande' (1-2 décadas bajo Rbp) lo
    deja mal centrado — por eso se prueban también potes más chicos.
    Garantiza 0<=wiper_frac<=1 (un pot en serie sólo puede sumar)."""
    pot_cands = [p for p in _POT_VALUES if rbp_ideal/1000.0 <= p <= rbp_ideal/3.0]
    return _rbp_pot_search(rbp_ideal, pot_cands) if pot_cands else None

def circuito_params(R1, R2, C1, C2):
    R2_eff = R2*Rin_bp_amp/(R2+Rin_bp_amp)
    tau1   = R1*C1; tau2 = R2_eff*C2
    if tau1 <= 0 or tau2 <= 0: return None, None
    return 1/(2*np.pi*np.sqrt(tau1*tau2)), (tau1+tau2)/(2*np.sqrt(tau1*tau2))

# Constantes precomputadas (deben estar antes de las funciones que las usan)
_H_IDEAL_EVAL = H_ideal_arr(FREQS_EVAL)          # notch × LP  → target final (con LP)
_H_NOTCH_EVAL = H_notch_arr(FREQS_EVAL)          # notch solo  → target del ajuste lstsq
_S_EVAL       = 1j*2*np.pi*FREQS_EVAL
_NIF_ADD_EVAL = (1+_S_EVAL/omega_p_add)/A0_add
_LOG_MAG_ID   = 20*np.log10(np.abs(_H_IDEAL_EVAL))
# Polo HF del adder (Rf||Cf).  Cf se elige siempre tal que Rf·Cf = 1/ω_cf
# (constante), así que este factor es el mismo para cualquier Rf comercial.
# NOTA: el ajuste lstsq de (a,b) se hace contra _H_NOTCH_EVAL (sin LP) — el LP
# ya está cascadeado en _H_IDEAL_EVAL Y se vuelve a aplicar via _L_CF_EVAL al
# formar H_approx/H_tot, así que ambos lados de la comparación final llevan
# el MISMO LP y se cancela exactamente (salvo el redondeo comercial de Cf).
# Ajustar contra _H_IDEAL_EVAL (con LP) sería aplicarlo dos veces.
_L_CF_EVAL    = 1.0/(1+_S_EVAL/OMEGA_CF)
_H_GEO_EVAL          = H_geo_arr(FREQS_EVAL, ZETA_GEO)
_LOG_TARGET_GEO_EVAL = 20*np.log10(np.abs(_H_GEO_EVAL * _H_IDEAL_EVAL))

def calibration_info(r):
    """Info de calibración consolidada para un resultado: Rbp óptimo (dado
    Ru/Rf ya fijos), su descomposición R_fija+R_pot, y el errRMS resultante
    (compensador solo y cascada con geófono) usando ese Rbp calibrado."""
    rbp_opt = rbp_optimal(r)
    pot     = rbp_fixed_pot(rbp_opt)
    rbp_cal = (pot['R_fixed']+pot['wiper_frac']*pot['R_pot']) if pot else rbp_opt
    err_comp_cal, K_cal = err_rms_comp_at(r, rbp_cal)
    full_e_cal     = _H_GEO_EVAL * H_comp_arr(r, FREQS_EVAL, rbp_cal)
    log_full_e_cal = 20*np.log10(np.abs(full_e_cal)) + K_cal
    diff_geo_cal   = _LOG_TARGET_GEO_EVAL - log_full_e_cal
    err_geo_cal    = float(np.sqrt(np.mean((diff_geo_cal-np.mean(diff_geo_cal))**2)))
    return dict(pot=pot, rbp_cal=rbp_cal, K_cal=K_cal,
                err_comp_cal=err_comp_cal, err_geo_cal=err_geo_cal)

# ════════════════════════════════════════════════════════════════════════════
# ADDER VECTORIZADO
# ════════════════════════════════════════════════════════════════════════════

def _ratios_opt(H_bp):
    X    = np.column_stack([np.ones(len(FREQS_EVAL)), H_bp])
    coef, _, _, _ = np.linalg.lstsq(X, _H_NOTCH_EVAL, rcond=None)
    return float(coef[0].real), float(coef[1].real)

def _cap_gains(a, b):
    """Si max(a,b) supera ADDER_GAIN_MAX, escala (a,b) por una constante k<1
    (preserva a/b y por lo tanto errRMS, que es invariante a escala) para que
    el mayor de los dos caiga justo en el límite.  Sin piso mínimo."""
    if a <= 0 or b <= 0: return None
    m = max(a, b)
    k = ADDER_GAIN_MAX/m if m > ADDER_GAIN_MAX else 1.0
    return a*k, b*k

def buscar_adder(H_bp, top_n=5):
    a_raw, b_raw = _ratios_opt(H_bp)
    capped = _cap_gains(a_raw, b_raw)
    if capped is None: return []
    a_opt, b_opt = capped
    Ru_id = _RES_ADD_ARR/a_opt; Rbp_id = _RES_ADD_ARR/b_opt
    mask  = ((Ru_id >= R_min_add) & (Ru_id <= R_max_add) &
             (Rbp_id >= R_min_add) & (Rbp_id <= R_max_add))
    Rf_v  = _RES_ADD_ARR[mask]
    if len(Rf_v) == 0: return []
    Ru_v  = np.array([res_comercial(v) for v in Rf_v/a_opt])
    Rbp_v = np.array([res_comercial(v) for v in Rf_v/b_opt])
    a_s   = Rf_v/Ru_v; b_s = Rf_v/Rbp_v
    valid = (a_s <= ADDER_GAIN_MAX) & (b_s <= ADDER_GAIN_MAX)
    Rf_v, Ru_v, Rbp_v, a_s, b_s = Rf_v[valid], Ru_v[valid], Rbp_v[valid], a_s[valid], b_s[valid]
    if len(Rf_v) == 0: return []
    a_v   = a_s[:,None]; b_v = b_s[:,None]
    Rf_2d = Rf_v[:,None];        nif = _NIF_ADD_EVAL[None,:]
    Av_u  = -a_v/(1+nif*(1+a_v+Rf_2d/Rin_add))
    Av_bp = -b_v/(1+nif*(1+b_v+Rf_2d/Rin_add))
    H_tot = (Av_u + Av_bp*H_bp[None,:]) * _L_CF_EVAL[None,:]
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
        Cf_ideal = 1.0/(2*np.pi*CF_TARGET_FC*Rf_v[i])
        Cf_act, Cf_type, Cf_a, Cf_b = cap_best_parallel(Cf_ideal)
        fc_act   = 1.0/(2*np.pi*Rf_v[i]*Cf_act)
        out.append(dict(Ru=float(Ru_v[i]), Rbp=float(Rbp_v[i]), Rf=float(Rf_v[i]),
                        Rbp_ideal=float(Rf_v[i]/b_opt),
                        Rf_type='S', a_real=float(Rf_v[i]/Ru_v[i]),
                        b_real=float(Rf_v[i]/Rbp_v[i]),
                        Cf_ideal=float(Cf_ideal), Cf_act=float(Cf_act),
                        Cf_type=Cf_type, Cf_a=Cf_a, Cf_b=Cf_b, fc_act=float(fc_act),
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
    # Hard constraint: R2 debe ser realizable con un resistor comercial único
    if R2 is None or R2 < R_min_bp or R2 > R2_max_single: return 1e6

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

    _denom = R1*C1 + R2*C2
    if _denom > 0:
        _gpeak = R2*C1/_denom
        if _gpeak > GAIN_BP_MAX or _gpeak < GAIN_BP_MIN: return 1e6

    H_bp = H_real_arr(R1, R2, C1, C2, FREQS_EVAL)
    X    = np.column_stack([np.ones(len(FREQS_EVAL)), H_bp])
    coef, _, _, _ = np.linalg.lstsq(X, _H_NOTCH_EVAL, rcond=None)
    capped = _cap_gains(coef[0].real, coef[1].real)               # a,b <= ADDER_GAIN_MAX
    if capped is None: return 1e3 + zeta_pen_w*zeta_pen
    a_opt, b_opt = capped

    H_approx = -(a_opt + b_opt*H_bp) * _L_CF_EVAL
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
    if R2_ideal < R_min_bp*0.5 or R2_ideal > R2_max_single*2: return None

    R2_act = res_comercial(R2_ideal)
    r2_err = abs(R2_act-R2_ideal)/R2_ideal*100
    r2_type = 'S'

    f0v, zv = circuito_params(R1_c, R2_act, C1_c, C2_val)
    if f0v is None: return None
    if abs(f0v-f0_des)/f0_des*100 > F0_TOL_PCT: return None
    if not (ZETA_MIN <= zv <= ZETA_MAX): return None
    _denom = R1_c*C1_c + R2_act*C2_val
    if _denom > 0:
        _gpeak = R2_act*C1_c/_denom
        if _gpeak > GAIN_BP_MAX or _gpeak < GAIN_BP_MIN: return None

    H_bp       = H_real_arr(R1_c, R2_act, C1_c, C2_val, FREQS_EVAL)
    adder_list = buscar_adder(H_bp, top_n=5)
    if not adder_list: return None
    ba = adder_list[0]
    return dict(R1=R1_c, C1=C1_c, C2=C2_val,
                C2_type=C2_type, C2_a=C2_a, C2_b=C2_b,
                R2_ideal=R2_ideal, R2_act=R2_act,
                r2_type=r2_type, r2_err=r2_err,
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

    # C2: por cada (R1_c, C1_c) agregar TODOS los caps que dan R2 en [R_min_bp, R2_max_single].
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
            C2_min = 1.0 / (omega0**2 * tau1 * R2_max_single)  # R2=max → C2=min
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
    _ZETA_MIDS = [810, 850, 890, 930, 970]
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

    # Seleccionar top con diversidad de ζ.  La condición de errRMS sólo separa
    # un pool de "mejores de los mejores" por banda — dentro de ese pool se
    # elige el candidato con la fase más plana de H_geo·H_total alrededor de
    # f0 (si hay margen para elegir; con 1 candidato no hay de otra).
    _ZETA_BANDS  = [(800,840),(840,880),(880,920),(920,960),(960,1001)]
    N_PHASE_POOL = 20   # candidatos de menor errRMS entre los que se elige por fase

    def _cand_key(v):
        return (v['R1'], v['C1'], round(v['C2'],30), round(v['R2_act'],1))

    def _pick_flattest(pool):
        if not pool: return None
        if len(pool) == 1: return pool[0]
        return min(pool, key=phase_flatness)

    results = []; seen_cand = set()
    for lo, hi in _ZETA_BANDS:
        pool = []
        for v in verified:
            if lo <= v['zeta'] < hi and _cand_key(v) not in seen_cand:
                pool.append(v)
                if len(pool) >= N_PHASE_POOL: break
        best = _pick_flattest(pool)
        if best is not None:
            results.append(best); seen_cand.add(_cand_key(best))
    while len(results) < TOP_N:
        pool = []
        for v in verified:
            if _cand_key(v) not in seen_cand:
                pool.append(v)
                if len(pool) >= N_PHASE_POOL: break
        best = _pick_flattest(pool)
        if best is None: break
        results.append(best); seen_cand.add(_cand_key(best))
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
    print(f"  BP: {GAIN_BP_MIN:.1f}<=gpeak<={GAIN_BP_MAX:.1f}  "
          f"Adder: a=Rf/Ru<={ADDER_GAIN_MAX:.0f}  b=Rf/Rbp<={ADDER_GAIN_MAX:.0f}  "
          f"Cf -> fc≈{CF_TARGET_FC:.3f}Hz  (eval hasta {F_EVAL_MAX:.0f}Hz)")
    print(f"  workers={N_WORKERS}  DE:{N_GEN_DE}gen×pop{POPSIZE_DE}  "
          f"bandas:{len(_ZETA_MIDS)}×{N_RESTARTS_BAND}restarts")
    print(SEP)
    hdr = (f"{'#':<3} {'R1':>9} {'R2':>9} {'C1':>8} {'C2':>9}"
           f"  {'Rbp':>9} {'Ru':>9} {'Rf':>9} {'Cf':>9} {'fc':>8}"
           f"  {'f0':>7} {'ζ':>7}"
           f"  {'errRMS':>8} {'errCAL':>8} {'K_dB':>7}"
           f"  {'Rbp=Rfijo+Rpot':>20}")
    print(hdr); print(sep)

    _C2_TAG = {'S': ' ', 'P': 'P', 'E': 'E'}
    for i, r in enumerate(results):
        ba  = r['best_adder']
        c2t = _C2_TAG.get(r['C2_type'], ' ')
        cft = _C2_TAG.get(ba['Cf_type'], ' ')
        ci  = calibration_info(r)
        pot = ci['pot']
        pot_s = (f"{fmt_res(pot['R_fixed'])}+{fmt_res(pot['R_pot'])}pot"
                 f"({pot['wiper_frac']*100:.0f}%)") if pot else "-"
        print(f"{i+1:<3} {fmt_res(r['R1']):>9} {fmt_res(r['R2_act']):>9}"
              f" {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>8}{c2t}"
              f"  {fmt_res(ba['Rbp']):>9} {fmt_res(ba['Ru']):>9} {fmt_res(ba['Rf']):>9}"
              f" {fmt_cap(ba['Cf_act']):>8}{cft} {fmt_hz(ba['fc_act']):>8}"
              f"  {r['f0']:>7.3f} {r['zeta']:>7.1f}"
              f"  {r['err_rms']:>6.3f}dB {ci['err_geo_cal']:>6.3f}dB"
              f" {10**(r['K_opt_dB']/20):>7.4f}x"
              f"  {pot_s:>20}")

    print(f"\n  (P=C2/Cf paralelo  E=C2 serie  R2 unico comercial sin T-red)")

    # ── CSV: resistencias, capacitores, calibración, f0/ζ, ganancias ────────────
    csv_path = os.path.join(script_dir, "compensador_optimo.csv")
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        header = ["rank",
                  "R1_kohm","R2_act_kohm",
                  "C1_uF",
                  "C2_pF","C2_type","C2_a_pF","C2_b_pF",
                  "Rbp_kohm","Ru_kohm","Rf_kohm",
                  "Cf_act_nF","Cf_type","Cf_a_nF","Cf_b_nF","fc_act_Hz",
                  "Rref_kohm",
                  "Rbp_fixed_kohm","Rbp_pot_kohm","Rbp_pot_wiper_frac",
                  "err_rms_dB","err_rms_cal_dB",
                  "f0_Hz","f0_err_pct","zeta",
                  "K_opt_lin",
                  "gain_BP_solo","gain_Adder_vs_BP","gain_Adder_vs_U","gain_Rf_par_RbpRu",
                  "gain_total_BPxAdder","gain_total_GeoxBPxAdder",
                  "PGA_min","PGA_max",
                  "gain_total_PGAxGeoxBPxAdder_min","gain_total_PGAxGeoxBPxAdder_max"]
        w.writerow(header)
        for i, r in enumerate(results):
            ba  = r['best_adder']
            ci  = calibration_info(r)
            pot = ci['pot']
            sg  = stage_gains_max(r)
            gain_geo_total = sg['max_geo_htot']
            row = [i+1,
                   round(r['R1']/1e3,4), round(r['R2_act']/1e3,4),
                   round(r['C1']*1e6,4),
                   round(r['C2']*1e12,4), r['C2_type'],
                   round(r['C2_a']*1e12,4) if r['C2_a'] else '',
                   round(r['C2_b']*1e12,4) if r['C2_b'] else '',
                   round(ba['Rbp']/1e3,4), round(ba['Ru']/1e3,4), round(ba['Rf']/1e3,4),
                   round(ba['Cf_act']*1e9,4), ba['Cf_type'],
                   round(ba['Cf_a']*1e9,4) if ba['Cf_a'] else '',
                   round(ba['Cf_b']*1e9,4) if ba['Cf_b'] else '',
                   round(ba['fc_act'],4),
                   round(r['Rref']/1e3,4),
                   round(pot['R_fixed']/1e3,4) if pot else '',
                   round(pot['R_pot']/1e3,4) if pot else '',
                   round(pot['wiper_frac'],4) if pot else '',
                   round(r['err_rms'],6), round(ci['err_geo_cal'],6),
                   r['f0'], round(r['f0_err'],4), round(r['zeta'],4),
                   round(10**(r['K_opt_dB']/20),6),
                   round(sg['gpeak_bp'],4), round(sg['avbp_rbp'],4), round(sg['avu_ru'],4),
                   round(sg['rf_par'],4), round(sg['max_htot'],4), round(gain_geo_total,6),
                   PGA_MIN, PGA_MAX,
                   round(gain_geo_total*PGA_MIN,6), round(gain_geo_total*PGA_MAX,6)]
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

        # Versión calibrada: Rbp = R_fija + R_pot en su posición óptima
        ci = calibration_info(r)
        H_tot_cal = H_comp_arr(r, freqs_plot, ci['rbp_cal'])
        mag_K_cal = 20*np.log10(np.abs(H_tot_cal)) + ci['K_cal']
        lbl_cal   = (f"#{i+1} ζ={r['zeta']:.0f} R1={fmt_res(r['R1'])} "
                     f"C1={fmt_cap(r['C1'])} C2={fmt_cap(r['C2'])} "
                     f"err={ci['err_comp_cal']:.2f}dB")
        ax_mag.semilogx(freqs_plot, mag_K_cal, color=col, lw=1.8, label=lbl_cal)
        ax_err.semilogx(freqs_plot, mag_K_cal-mag_id, color=col, lw=1.5,
                        label=f"#{i+1} ζ={r['zeta']:.0f}")

    ax_mag.axvline(f0_des, color='gray', ls=':', lw=1)
    ax_mag.set_ylabel('Magnitud (dB)'); ax_mag.legend(loc='lower right', fontsize=6)
    ax_mag.set_title('Compensador Óptimo calibrado (Rbp=Rfijo+Rpot)\n'
                      'K·|H_total| vs H_ideal')
    ax_mag.grid(True, which='both', ls=':', alpha=0.4)
    ax_mag.set_xlim([freqs_plot[0], freqs_plot[-1]])
    ax_err.axhline(0, color='k', lw=0.8); ax_err.axvline(f0_des, color='gray', ls=':', lw=1)
    ax_err.set_xlabel('Frecuencia (Hz)'); ax_err.set_ylabel('Error (dB)')
    ax_err.legend(fontsize=7); ax_err.grid(True, which='both', ls=':', alpha=0.4)
    plt.tight_layout()
    plt.savefig(os.path.join(script_dir, "compensador_optimo.png"), dpi=150)

    # ── Gráfica 2: cascada con el geófono real ───────────────────────────────────
    # H_geo_real · H_notch = H_geo_target  (exacto, algebraico) →
    # target_full = H_geo_real · H_ideal = H_geo_target · H_lp
    # actual_full = H_geo_real · H_total_circuito_real  (con Zf=Rf||Cf_act, nif)
    print(f"\n  Geófono: m={M_GEOPHONE}kg  k={K_GEOPHONE:.3f}N/m  d={D_GEOPHONE:.5f}Ns/m  "
          f"wn={omega0:.3f}rad/s  hi={ZETA_GEO}", flush=True)

    H_geo_plt   = H_geo_arr(freqs_plot, ZETA_GEO)
    target_full = H_geo_plt * H_id_plt                 # = H_geo_target · H_lp
    mag_target  = 20*np.log10(np.abs(target_full))
    phase_target = np.degrees(np.unwrap(np.angle(target_full)))

    # Normalizar todas las curvas (mag y fase) a 0 en f0_des — facilita comparar
    # formas entre curvas que de otro modo arrancan en niveles/fases arbitrarios.
    idx0 = int(np.argmin(np.abs(freqs_plot - f0_des)))
    mag_target_n   = mag_target - mag_target[idx0]
    phase_target_n = phase_target - phase_target[idx0]

    fig2, (ax_m2, ax_p2) = plt.subplots(2,1, figsize=(15,10), sharex=True)
    ax_m2.semilogx(freqs_plot, mag_target_n, 'k--', lw=2.5,
                   label='H_geo_target·LP (ref, hi→ζ_target)')
    ax_p2.semilogx(freqs_plot, phase_target_n, 'k--', lw=2.5,
                   label='H_geo_target·LP (ref)')

    print(f"  {'#':<3} {'ζ':>7}  {'errRMS_comp':>11}  {'errRMS_geo CAL':>14}")
    for i in range(N_PLOT):
        r   = results[i]; col = colores[i%len(colores)]; ba = r['best_adder']
        ci  = calibration_info(r)

        full_cal     = H_geo_cascade_arr(r, freqs_plot, ci['rbp_cal'])
        mag_cal_n    = 20*np.log10(np.abs(full_cal));   mag_cal_n   -= mag_cal_n[idx0]
        phase_cal_n  = np.degrees(np.unwrap(np.angle(full_cal))); phase_cal_n -= phase_cal_n[idx0]

        print(f"  {i+1:<3} {r['zeta']:>7.1f}  {r['err_rms']:>10.3f}dB  "
              f"{ci['err_geo_cal']:>13.3f}dB")

        lbl_cal = (f"#{i+1} ζ={r['zeta']:.0f} errRMS_geo={ci['err_geo_cal']:.2f}dB")
        ax_m2.semilogx(freqs_plot, mag_cal_n, color=col, lw=1.8, label=lbl_cal)
        ax_p2.semilogx(freqs_plot, phase_cal_n, color=col, lw=1.5,
                       label=f"#{i+1} ζ={r['zeta']:.0f}")

    ax_m2.axvline(f0_des, color='gray', ls=':', lw=1)
    ax_m2.axvline(CF_TARGET_FC, color='purple', ls=':', lw=1, label=f'fc≈{CF_TARGET_FC:.0f}Hz')
    ax_m2.set_ylabel(f'Magnitud normalizada (dB, 0 en f0={f0_des}Hz)')
    ax_m2.legend(loc='lower left', fontsize=6)
    ax_m2.set_title('Cascada Geófono × Compensador calibrado (Rbp=Rfijo+Rpot, normalizado en f0)\n'
                     '|H_geo·H_total| vs H_geo_target·LP')
    ax_m2.grid(True, which='both', ls=':', alpha=0.4)
    ax_m2.set_xlim([freqs_plot[0], freqs_plot[-1]])
    ax_p2.axhline(0, color='k', lw=0.5); ax_p2.axvline(f0_des, color='gray', ls=':', lw=1)
    ax_p2.axvline(CF_TARGET_FC, color='purple', ls=':', lw=1)
    ax_p2.set_xlabel('Frecuencia (Hz)')
    ax_p2.set_ylabel(f'Fase desenrollada (°, 0 en f0={f0_des}Hz)')
    ax_p2.legend(fontsize=6); ax_p2.grid(True, which='both', ls=':', alpha=0.4)
    plt.tight_layout()
    plt.savefig(os.path.join(script_dir, "compensador_optimo_geofono.png"), dpi=150)
    print(f"  Grafico cascada guardado: "
          f"{os.path.join(script_dir, 'compensador_optimo_geofono.png')}", flush=True)

    plt.show()
