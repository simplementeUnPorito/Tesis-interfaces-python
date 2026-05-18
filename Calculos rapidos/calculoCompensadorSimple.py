# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import warnings
import csv
import os
import time
warnings.filterwarnings('ignore')

try:
    from tqdm import tqdm
except ImportError:
    # Fallback sin tqdm: imprime progreso cada 5 % y ETA manual
    class tqdm:
        def __init__(self, iterable=None, total=None, desc='', unit='it', ncols=None, **kw):
            self._it    = list(iterable) if iterable is not None else []
            self._total = total if total is not None else len(self._it)
            self._desc  = desc
            self._unit  = unit
            self._n     = 0
            self._t0    = time.time()
            self._last_pct = -1

        def __iter__(self):
            for item in self._it:
                yield item
                self._n += 1
                pct = int(100 * self._n / self._total) if self._total else 0
                if pct != self._last_pct and pct % 5 == 0:
                    elapsed = time.time() - self._t0
                    eta = elapsed / self._n * (self._total - self._n) if self._n else 0
                    print(f"\r  {self._desc}: {pct:3d}%  "
                          f"[{self._n}/{self._total} {self._unit}]  "
                          f"elapsed {elapsed:.0f}s  ETA {eta:.0f}s      ",
                          end='', flush=True)
                    self._last_pct = pct
            print(flush=True)

        def set_postfix_str(self, s): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN — todas las variables de diseño aquí arriba
# ════════════════════════════════════════════════════════════════════════════

# ── Geófono ─────────────────────────────────────────────────────────────────
f0_des      = 10       # Hz — frecuencia natural
ZETA_GEO    = 0.25     # amortiguamiento natural
ZETA_TARGET = 1000     # amortiguamiento objetivo de compensación
omega0      = 2*np.pi * f0_des

# ── Op-amp del BP ────────────────────────────────────────────────────────────
A0_dB_bp    = 90                       # dB — ganancia DC en lazo abierto
A0_bp       = 10**(A0_dB_bp / 20.0)   # lineal ≈ 31623
f_p_bp      = 8e6                      # Hz — polo dominante
omega_p_bp  = 2*np.pi * f_p_bp
Rin_bp_amp  = 35e6                     # Ω — resistencia de entrada al nodo inversor

# ── Op-amp del Adder (separado para cambiar en el futuro) ───────────────────
A0_dB_add   = 90
A0_add      = 10**(A0_dB_add / 20.0)
f_p_add     = 8e6                      # Hz
omega_p_add = 2*np.pi * f_p_add
Rin_add     = 35e6                     # Ω

# ── Rango de búsqueda — BP ──────────────────────────────────────────────────
R_min_bp    = 1e3      # Ω
R_max_bp    = 1e6      # Ω
F0_TOL_PCT  = 10.0     # % error máximo en f0 aceptable
ZETA_MIN    = 100      # ζ mínimo buscado en BP
ZETA_MAX    = 1000     # ζ máximo buscado en BP
TOP_N       = 15       # candidatos finales a mostrar

# ── Rango de búsqueda — Adder (separado para cambiar en el futuro) ──────────
R_min_add   = 1e3      # Ω
R_max_add   = 1e6      # Ω

# ── Frecuencias de evaluación ────────────────────────────────────────────────
FREQS_EVAL  = np.logspace(np.log10(f0_des/1000), np.log10(1e5), 800)

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

_RES_BP   = [r for r in RES_COMERCIALES if R_min_bp  <= r <= R_max_bp]
_RES_ADD  = [r for r in RES_COMERCIALES if R_min_add <= r <= R_max_add]

CAPS_TODOS     = list(CAP_COMERCIALES)
CAPS_CERAMICOS = [c for c in CAP_COMERCIALES if c <= 100e-9]

# ════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ════════════════════════════════════════════════════════════════════════════

def fmt_cap(c):
    if c >= 1e-6: return f"{c*1e6:.2f}uF"
    if c >= 1e-9: return f"{c*1e9:.2f}nF"
    return f"{c*1e12:.2f}pF"

def fmt_res(r):
    if r >= 1e6: return f"{r/1e6:.3f}Mohm"
    if r >= 1e3: return f"{r/1e3:.3f}kohm"
    return f"{r:.1f}ohm"

def cap_comercial(v):
    return CAP_COMERCIALES[np.argmin([abs(c-v) for c in CAP_COMERCIALES])]

def res_comercial(v):
    return RES_COMERCIALES[np.argmin([abs(r-v) for r in RES_COMERCIALES])]

# ════════════════════════════════════════════════════════════════════════════
# T-REDES DE RESISTENCIAS — R2 (BP)
#   Topología: Vout ─Ra─ M ─Rb─ V_minus,  Rc de M a GND
#   R2_eq = Ra + Rb + Ra·Rb/Rc
# ════════════════════════════════════════════════════════════════════════════
_t0 = time.time()
print("  Precomputando tabla de T-redes R2 (BP)...", flush=True)
_Ra_max_bp = max(_RES_BP)
_Rc_min_bp = min(_RES_BP)
R2_max_tred = 2*_Ra_max_bp + _Ra_max_bp**2/_Rc_min_bp

_tred_entries = []
for _Ra in _RES_BP:
    for _Rb in _RES_BP:
        for _Rc in _RES_BP:
            _tred_entries.append((_Ra + _Rb + _Ra*_Rb/_Rc, _Ra, _Rb, _Rc))
_tred_entries.sort(key=lambda x: x[0])
_TRED_R2_ARR = np.array([e[0] for e in _tred_entries])

def mejores_tred(R2_target, top_n=5):
    idx0   = int(np.searchsorted(_TRED_R2_ARR, R2_target))
    window = 300
    lo     = max(0, idx0 - window)
    hi     = min(len(_tred_entries), idx0 + window)
    cands  = sorted(_tred_entries[lo:hi], key=lambda e: abs(e[0] - R2_target))
    seen   = set()
    out    = []
    for R2_eq, Ra, Rb, Rc in cands:
        norm = (min(Ra,Rb), max(Ra,Rb), Rc)
        if norm not in seen:
            seen.add(norm)
            out.append(dict(Ra=Ra, Rb=Rb, Rc=Rc, R2_eq=R2_eq,
                            err=abs(R2_eq-R2_target)/R2_target*100))
        if len(out) >= top_n: break
    return out

# ════════════════════════════════════════════════════════════════════════════
# T-REDES DE RESISTENCIAS — Rf (Adder)
# ════════════════════════════════════════════════════════════════════════════
print(f"  T-redes R2 listas ({len(_tred_entries)} entradas, {time.time()-_t0:.1f}s)", flush=True)
_t0 = time.time()
print("  Precomputando tabla de T-redes Rf (Adder)...", flush=True)
_tred_Rf_entries = []
for _Ra in _RES_ADD:
    for _Rb in _RES_ADD:
        for _Rc in _RES_ADD:
            _tred_Rf_entries.append((_Ra + _Rb + _Ra*_Rb/_Rc, _Ra, _Rb, _Rc))
_tred_Rf_entries.sort(key=lambda x: x[0])
_TRED_Rf_ARR = np.array([e[0] for e in _tred_Rf_entries])

def mejores_tred_Rf(Rf_target, top_n=5):
    idx0   = int(np.searchsorted(_TRED_Rf_ARR, Rf_target))
    window = 300
    lo     = max(0, idx0 - window)
    hi     = min(len(_tred_Rf_entries), idx0 + window)
    cands  = sorted(_tred_Rf_entries[lo:hi], key=lambda e: abs(e[0] - Rf_target))
    seen   = set()
    out    = []
    for Rf_eq, Ra, Rb, Rc in cands:
        norm = (min(Ra,Rb), max(Ra,Rb), Rc)
        if norm not in seen:
            seen.add(norm)
            out.append(dict(Ra=Ra, Rb=Rb, Rc=Rc, Rf_eq=Rf_eq,
                            err=abs(Rf_eq-Rf_target)/Rf_target*100))
        if len(out) >= top_n: break
    return out

# ════════════════════════════════════════════════════════════════════════════
# C2 EXTENDIDO: individual + paralelo (más grande) + serie (más chico)
#   Paralelo: C_eq = Ca + Cb   → accede a valores más grandes / mejor precisión
#   Serie:    C_eq = Ca·Cb/(Ca+Cb) → accede a valores más chicos
# ════════════════════════════════════════════════════════════════════════════
def _build_c2_ext(caps_list):
    """Retorna lista de (c2_val, tipo, ca, cb) para todos los C2 alcanzables."""
    seen   = {}   # valor → primer tipo encontrado (S > P > E)
    result = []
    for c in caps_list:
        if c not in seen:
            seen[c] = True
            result.append((c, 'S', None, None))
    for ca in caps_list:
        for cb in caps_list:
            if ca <= cb:
                cp = ca + cb
                if cp not in seen:
                    seen[cp] = True
                    result.append((cp, 'P', ca, cb))
                cs = ca*cb / (ca+cb)
                if cs not in seen:
                    seen[cs] = True
                    result.append((cs, 'E', ca, cb))
    return result   # (valor, tipo, ca, cb)

print(f"  T-redes Rf listas ({len(_tred_Rf_entries)} entradas, {time.time()-_t0:.1f}s)", flush=True)
_t0 = time.time()
print("  Precomputando C2 ext. (paralelo+serie) para TODOS...", flush=True)
_C2_EXT_TODOS = _build_c2_ext(CAPS_TODOS)
print(f"  C2 TODOS listo ({len(_C2_EXT_TODOS)} vals, {time.time()-_t0:.1f}s)", flush=True)
_t0 = time.time()
print("  Precomputando C2 ext. (paralelo+serie) para CERÁMICOS...", flush=True)
_C2_EXT_CERAM = _build_c2_ext(CAPS_CERAMICOS)
print(f"  C2 CERÁMICOS listo ({len(_C2_EXT_CERAM)} vals, {time.time()-_t0:.1f}s)", flush=True)

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE TRANSFERENCIA
#
# BP (inversor):
#   β(s) = sR2C1 / ((sR1C1+1)(sR2C2+1))
#   Av_BP(s) = -β / [1 + ((1+s/ωp_bp)/A0_bp)·(1 + β + R2/(Rin_bp·(sR2C2+1)))]
#
# Adder (superposición, cada brazo con su propio denominador no-ideal):
#   Av_u  = -(Rf/Ru)  / [1 + nif_add·(1 + Rf/Ru  + Rf/Rin_add)]
#   Av_bp = -(Rf/Rbp) / [1 + nif_add·(1 + Rf/Rbp + Rf/Rin_add)]
#   H_total = Av_u·1 + Av_bp·H_BP_real     (= −K·H_ideal en caso ideal con Ru=Rbp)
#   nif_add = (1+s/ωp_add)/A0_add
#
# Objetivo:  K · |H_adder| ≈ |H_ideal|
#   H_ideal(s) = (s²+2·ζ₀·ω₀·s+ω₀²) / (s²+2·ζ₁·ω₀·s+ω₀²)
# ════════════════════════════════════════════════════════════════════════════

def H_real_arr(R1, R2, C1, C2, freqs_hz):
    s      = 1j * 2*np.pi * freqs_hz
    beta   = (s*R2*C1) / ((s*R1*C1+1) * (s*R2*C2+1))
    z_load = (R2/Rin_bp_amp) / (s*R2*C2+1)
    nif    = (1 + s/omega_p_bp) / A0_bp
    return -beta / (1 + nif*(1 + beta + z_load))

def H_ideal_arr(freqs_hz):
    s = 1j * 2*np.pi * freqs_hz
    return (s**2 + 2*ZETA_GEO*omega0*s + omega0**2) / \
           (s**2 + 2*ZETA_TARGET*omega0*s + omega0**2)

def H_deseada_arr(freqs_hz):
    """BP deseado (referencia de diseño del BP solo)."""
    s   = 1j * 2*np.pi * freqs_hz
    num = -2*(ZETA_TARGET - ZETA_GEO)*omega0*s
    den = s**2 + 2*ZETA_TARGET*omega0*s + omega0**2
    return num / den

def H_total_arr(R1, R2, C1, C2, Ru, Rbp, Rf, freqs_hz):
    """TF completa: BP no-ideal → sumador no-ideal (superposición).
    Av_u  = -(Rf/Ru)  / [1 + nif·(1 + Rf/Ru  + Rf/Rin)]
    Av_bp = -(Rf/Rbp) / [1 + nif·(1 + Rf/Rbp + Rf/Rin)]
    H_total = Av_u·1 + Av_bp·H_BP_real
    """
    s    = 1j * 2*np.pi * freqs_hz
    nif  = (1 + s/omega_p_add) / A0_add
    H_bp = H_real_arr(R1, R2, C1, C2, freqs_hz)
    Av_u  = -(Rf/Ru)  / (1 + nif*(1 + Rf/Ru  + Rf/Rin_add))
    Av_bp = -(Rf/Rbp) / (1 + nif*(1 + Rf/Rbp + Rf/Rin_add))
    return Av_u + Av_bp*H_bp

_H_IDEAL_EVAL  = H_ideal_arr(FREQS_EVAL)          # precomputada
_S_EVAL        = 1j * 2*np.pi * FREQS_EVAL        # s para FREQS_EVAL
_NIF_ADD_EVAL  = (1 + _S_EVAL/omega_p_add)/A0_add # nif adder para FREQS_EVAL
_LOG_MAG_ID    = 20*np.log10(np.abs(_H_IDEAL_EVAL))  # precomputado en dB
_RES_ADD_ARR   = np.array(_RES_ADD, dtype=float)  # array numpy para vectorización

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE ERROR Y PARÁMETROS DE CIRCUITO
# ════════════════════════════════════════════════════════════════════════════

def rms_error_compensador_from_bp(H_bp, Ru, Rbp, Rf, s, nif_add):
    """Error RMS (dB) del sistema completo vs H_ideal usando modelo superposición.
    H_total = Av_u + Av_bp·H_bp  (cada brazo con su propio denominador no-ideal)
    Retorna (err_rms, K_opt_dB).
    """
    Av_u  = -(Rf/Ru)  / (1 + nif_add*(1 + Rf/Ru  + Rf/Rin_add))
    Av_bp = -(Rf/Rbp) / (1 + nif_add*(1 + Rf/Rbp + Rf/Rin_add))
    H_tot  = Av_u + Av_bp*H_bp
    mag_t  = np.abs(H_tot)
    mag_id = np.abs(_H_IDEAL_EVAL)
    mask   = (mag_t > 1e-30) & (mag_id > 1e-30)
    if not np.any(mask): return np.inf, 0.0
    diff     = 20*np.log10(mag_id[mask]) - 20*np.log10(mag_t[mask])
    K_opt_dB = float(np.mean(diff))
    return float(np.sqrt(np.mean((diff - K_opt_dB)**2))), K_opt_dB

def circuito_params(R1, R2, C1, C2):
    R2_eff = R2*Rin_bp_amp / (R2+Rin_bp_amp)
    tau1   = R1*C1
    tau2   = R2_eff*C2
    if tau1 <= 0 or tau2 <= 0: return None, None
    w0   = 1.0 / np.sqrt(tau1*tau2)
    zeta = (tau1+tau2) / (2*np.sqrt(tau1*tau2))
    return w0/(2*np.pi), zeta

# ════════════════════════════════════════════════════════════════════════════
# BÚSQUEDA DE RESISTENCIAS DEL ADDER
# ════════════════════════════════════════════════════════════════════════════

def ratios_optimos_adder(H_bp):
    """
    Modelo superposición ideal: H_total = -(a + b·H_BP)  ≈  -K·H_ideal
    → a + b·H_BP ≈ K·H_ideal
    LS complejo con K=1: [1, H_BP]·[a,b]ᵀ = H_ideal
    Devuelve (a_opt, b_opt) reales (partes reales del resultado LS).
    """
    X    = np.column_stack([np.ones(len(FREQS_EVAL)), H_bp])
    coef, _, _, _ = np.linalg.lstsq(X, _H_IDEAL_EVAL, rcond=None)
    return float(coef[0].real), float(coef[1].real)

def buscar_adder(H_bp, top_n=5):
    """
    Búsqueda vectorizada de (Ru, Rbp, Rf) comerciales para el BP dado.
    Recibe H_bp precomputado para evitar recalcularlo por cada Rf.
    NO usa T-redes en el loop principal — se refinan después solo para top-N.
    """
    a_opt, b_opt = ratios_optimos_adder(H_bp)
    if a_opt <= 0 or b_opt <= 0:
        return []

    # Filtrar Rf factibles en vectores numpy
    Ru_ideal  = _RES_ADD_ARR / a_opt   # (n_Rf,)
    Rbp_ideal = _RES_ADD_ARR / b_opt   # (n_Rf,)
    mask = ((Ru_ideal  >= R_min_add) & (Ru_ideal  <= R_max_add) &
            (Rbp_ideal >= R_min_add) & (Rbp_ideal <= R_max_add))
    Rf_v  = _RES_ADD_ARR[mask]
    if len(Rf_v) == 0:
        return []

    Ru_v  = np.array([res_comercial(v) for v in (Rf_v/a_opt)])
    Rbp_v = np.array([res_comercial(v) for v in (Rf_v/b_opt)])

    # Vectorizar evaluación: broadcast (n_Rf, n_freq)
    a_v   = (Rf_v/Ru_v)[:, None]       # (n_Rf, 1)
    b_v   = (Rf_v/Rbp_v)[:, None]      # (n_Rf, 1)
    Rf_2d = Rf_v[:, None]               # (n_Rf, 1)
    nif   = _NIF_ADD_EVAL[None, :]      # (1, n_freq)
    Hbp   = H_bp[None, :]              # (1, n_freq)

    Av_u  = -a_v  / (1 + nif*(1 + a_v  + Rf_2d/Rin_add))   # (n_Rf, n_freq)
    Av_bp = -b_v  / (1 + nif*(1 + b_v  + Rf_2d/Rin_add))   # (n_Rf, n_freq)
    H_tot = Av_u + Av_bp * Hbp                               # (n_Rf, n_freq)

    mag_t = np.abs(H_tot)              # (n_Rf, n_freq)
    valid = (mag_t > 1e-30)            # (n_Rf, n_freq)
    with np.errstate(divide='ignore', invalid='ignore'):
        log_t = np.where(valid, 20*np.log10(mag_t), np.nan)

    diff     = _LOG_MAG_ID[None, :] - log_t          # (n_Rf, n_freq)
    K_dB     = np.nanmean(diff, axis=1)              # (n_Rf,)
    err_rms  = np.sqrt(np.nanmean((diff - K_dB[:,None])**2, axis=1))  # (n_Rf,)

    # Deduplicar por (Ru, Rbp) y devolver top_n
    order = np.argsort(err_rms)
    seen  = set()
    out   = []
    for i in order:
        key = (int(Ru_v[i]), int(Rbp_v[i]))
        if key in seen: continue
        seen.add(key)
        out.append(dict(
            Ru=float(Ru_v[i]), Rbp=float(Rbp_v[i]), Rf=float(Rf_v[i]),
            Rf_type='S',
            a_real=float(Rf_v[i]/Ru_v[i]), b_real=float(Rf_v[i]/Rbp_v[i]),
            err_rms=float(err_rms[i]), K_opt_dB=float(K_dB[i])
        ))
        if len(out) >= top_n: break
    return out


def refinar_adder_tred(r, top_n=5):
    """
    Refina las opciones del adder del resultado r añadiendo T-redes para Rf.
    Llamar solo sobre los resultados finales (TOP_N), no en el loop principal.
    """
    H_bp = H_real_arr(r['R1'], r['R2_act'], r['C1'], r['C2'], FREQS_EVAL)
    a_opt, b_opt = ratios_optimos_adder(H_bp)
    if a_opt <= 0 or b_opt <= 0:
        return r['adder_list']

    existentes = {(ad['Ru'], ad['Rbp'], round(ad['Rf'],2)) for ad in r['adder_list']}
    extras     = []
    idx_lo = int(np.searchsorted(_TRED_Rf_ARR, R_min_add))
    idx_hi = int(np.searchsorted(_TRED_Rf_ARR, R_max_add * 3))
    for R_eq, Ra, Rb, Rc in _tred_Rf_entries[idx_lo:idx_hi]:
        Ru_id  = R_eq / a_opt
        Rbp_id = R_eq / b_opt
        if not (R_min_add <= Ru_id <= R_max_add): continue
        if not (R_min_add <= Rbp_id <= R_max_add): continue
        Ru_c  = res_comercial(Ru_id)
        Rbp_c = res_comercial(Rbp_id)
        key   = (Ru_c, Rbp_c, round(R_eq, 2))
        if key in existentes: continue
        existentes.add(key)
        err, K_dB = rms_error_compensador_from_bp(H_bp, Ru_c, Rbp_c,
                                                   R_eq, _S_EVAL, _NIF_ADD_EVAL)
        extras.append(dict(Ru=Ru_c, Rbp=Rbp_c, Rf=R_eq, Rf_type='T',
                           Rf_tred=(Ra, Rb, Rc),
                           a_real=R_eq/Ru_c, b_real=R_eq/Rbp_c,
                           err_rms=err, K_opt_dB=K_dB))
    combined = r['adder_list'] + extras
    combined.sort(key=lambda x: x['err_rms'])
    return combined[:top_n]

# ════════════════════════════════════════════════════════════════════════════
# FUNCIÓN DE BÚSQUEDA PRINCIPAL
# ════════════════════════════════════════════════════════════════════════════

_ZETA_BANDS = [(100,200),(200,300),(300,400),(400,500),
               (500,600),(600,700),(700,800),(800,900),(900,1001)]

def _cand_key(v):
    return (v['R1'], v['C1'], round(v['C2'],25), round(v['R2_act'],1))

def buscar(caps_search, label, c2_ext_list=None):
    """
    c2_ext_list: lista de (c2_val, tipo, ca, cb) — si None se construye desde caps_search.
    """
    if c2_ext_list is None:
        c2_ext_list = _build_c2_ext(caps_search)

    n_r1   = len(_RES_BP)
    n_c2   = len(c2_ext_list)
    phase1 = []
    pbar1  = tqdm(_RES_BP,
                  total=n_r1,
                  desc=f"[{label}] Fase 1  ({n_c2} vals C2)",
                  unit="R1", ncols=110)
    for R1 in pbar1:
        for C1 in caps_search:
            tau1 = R1*C1
            for C2_val, C2_type, C2_a, C2_b in c2_ext_list:
                tau2_target = 1.0 / (omega0**2 * tau1)
                R2_i = tau2_target / C2_val
                if R2_i < R_min_bp or R2_i > R2_max_tred:
                    continue
                R2_eff = R2_i*Rin_bp_amp / (R2_i+Rin_bp_amp)
                tau2   = R2_eff*C2_val
                zeta   = (tau1+tau2) / (2*np.sqrt(tau1*tau2))
                if not (ZETA_MIN <= zeta <= ZETA_MAX):
                    continue
                phase1.append(dict(R1=R1, C1=C1, C2=C2_val,
                                   C2_type=C2_type, C2_a=C2_a, C2_b=C2_b,
                                   R2_ideal=R2_i, zeta_approx=zeta))
        pbar1.set_postfix_str(f"candidatos={len(phase1)}")
    print(f"  [{label}] Fase 1 completa: {len(phase1)} candidatos.", flush=True)

    verified  = []
    seen_keys = set()
    pbar2 = tqdm(phase1,
                 total=len(phase1),
                 desc=f"[{label}] Fase 2  (adder)",
                 unit="cand", ncols=110)
    for cand in pbar2:
        R1   = cand['R1'];  C1 = cand['C1'];  C2 = cand['C2']
        R2_i = cand['R2_ideal']

        # ── Cuantizar R2 ────────────────────────────────────────────────
        R2_c       = res_comercial(R2_i)
        err_single = abs(R2_c-R2_i)/R2_i*100
        tred_list  = mejores_tred(R2_i, top_n=5)
        tred_best  = tred_list[0] if tred_list else None
        err_tred   = tred_best['err'] if tred_best else float('inf')

        if tred_best and err_tred < err_single:
            R2_act  = tred_best['R2_eq']; r2_type = 'T'; r2_err = err_tred
        else:
            R2_act  = R2_c;               r2_type = 'S'; r2_err = err_single

        key = (R1, C1, round(C2,25), round(R2_act,1))
        if key in seen_keys: continue
        seen_keys.add(key)

        # ── Verificar f0 y ζ del BP ─────────────────────────────────────
        f0v, zv = circuito_params(R1, R2_act, C1, C2)
        if f0v is None: continue
        f0_err = abs(f0v-f0_des)/f0_des*100
        if f0_err > F0_TOL_PCT: continue
        if not (ZETA_MIN <= zv <= ZETA_MAX): continue

        # ── Buscar adder (Ru, Rbp, Rf) — vectorizado, sin T-red ─────────
        H_bp_cand  = H_real_arr(R1, R2_act, C1, C2, FREQS_EVAL)
        adder_list = buscar_adder(H_bp_cand, top_n=5)
        if not adder_list: continue

        best_adder = adder_list[0]
        err_rms    = best_adder['err_rms']
        K_opt_dB   = best_adder['K_opt_dB']
        Rref       = res_comercial(R1*R2_act/(R1+R2_act))

        verified.append(dict(
            R1=R1, C1=C1, C2=C2,
            C2_type=cand['C2_type'], C2_a=cand['C2_a'], C2_b=cand['C2_b'],
            R2_ideal=R2_i, R2_act=R2_act,
            r2_type=r2_type, r2_err=r2_err, tred_list=tred_list,
            f0=f0v, f0_err=f0_err, zeta=zv,
            err_rms=err_rms, K_opt_dB=K_opt_dB,
            K_opt_lin=10**(K_opt_dB/20),
            adder_list=adder_list, best_adder=best_adder,
            zeta_approx=cand['zeta_approx'], Rref=Rref
        ))
        best_so_far = min(v['err_rms'] for v in verified)
        pbar2.set_postfix_str(f"verified={len(verified)}  best={best_so_far:.3f}dB")

    print(f"  [{label}] Fase 2 completa: {len(verified)} candidatos verificados.", flush=True)
    verified.sort(key=lambda x: (x['err_rms'], x['f0_err']))

    results   = []
    seen_cand = set()
    for lo, hi in _ZETA_BANDS:
        for v in verified:
            if lo <= v['zeta'] < hi and _cand_key(v) not in seen_cand:
                results.append(v); seen_cand.add(_cand_key(v)); break
    for v in verified:
        if len(results) >= TOP_N: break
        if _cand_key(v) not in seen_cand:
            results.append(v); seen_cand.add(_cand_key(v))

    results.sort(key=lambda x: x['zeta'])
    print(f"  [{label}] Top {len(results)} seleccionados de {len(verified)} verificados.",
          flush=True)
    return verified, results

# ════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN
# ════════════════════════════════════════════════════════════════════════════

_t_inicio = time.time()
verified_todos,     results_todos     = buscar(CAPS_TODOS,     "TODOS",     _C2_EXT_TODOS)
verified_ceramicos, results_ceramicos = buscar(CAPS_CERAMICOS, "CERÁMICOS", _C2_EXT_CERAM)
print(f"\n  Búsqueda completada en {time.time()-_t_inicio:.1f}s", flush=True)

# ── Refinamiento con T-redes Rf solo para el top-N final ────────────────────
print("  Refinando top candidatos con T-redes Rf...", flush=True)
for r in tqdm(results_todos + results_ceramicos,
              desc="  Refinamiento T-red Rf", unit="cand", ncols=90):
    r['adder_list'] = refinar_adder_tred(r, top_n=5)
    r['best_adder'] = r['adder_list'][0]
    r['err_rms']    = r['best_adder']['err_rms']
    r['K_opt_dB']   = r['best_adder']['K_opt_dB']
    r['K_opt_lin']  = 10**(r['K_opt_dB']/20)
# Re-ordenar después del refinamiento
results_todos.sort(key=lambda x: x['zeta'])
results_ceramicos.sort(key=lambda x: x['zeta'])
print(f"  Refinamiento completado en {time.time()-_t_inicio:.1f}s total.", flush=True)

GRUPOS = [
    ("TODOS LOS CAPACITORES",    "todos",     verified_todos,     results_todos),
    ("SOLO CERÁMICOS (≤ 100nF)", "ceramicos", verified_ceramicos, results_ceramicos),
]

# ════════════════════════════════════════════════════════════════════════════
# OUTPUT, CSV Y GRÁFICAS
# ════════════════════════════════════════════════════════════════════════════
import matplotlib.pyplot as plt

SEP        = "=" * 150
sep        = "-" * 150
script_dir = os.path.dirname(os.path.abspath(__file__))
freqs_plot = np.logspace(-3, 6, 3000)
colores    = ['royalblue','forestgreen','firebrick','darkorange',
              'purple','teal','brown','crimson']

for grupo_label, grupo_slug, verified, results in GRUPOS:

    if not results:
        print(f"\n  [{grupo_label}] Sin resultados.")
        continue

    # ── Tabla principal ──────────────────────────────────────────────────
    print(); print(SEP)
    print(f"  GRUPO: {grupo_label}")
    print("  Objetivo: K·|H_adder(BP+Sumador)| ≈ |H_ideal| = (s²+2ζ₀ω₀s+ω₀²)/(s²+2ζ₁ω₀s+ω₀²)")
    print(f"  ζ_geo={ZETA_GEO}  ζ_target={ZETA_TARGET}  f0={f0_des}Hz")
    print(f"  BP op-amp: A0={A0_dB_bp}dB  fp={f_p_bp/1e6:.0f}MHz  Rin={fmt_res(Rin_bp_amp)}")
    print(f"  Add op-amp: A0={A0_dB_add}dB  fp={f_p_add/1e6:.0f}MHz  Rin={fmt_res(Rin_add)}")
    print(SEP)
    hdr = (f"{'#':<3} {'C1':>8} {'C2':>10} {'R1':>10} {'R2_act':>11}"
           f"  {'f0':>7} {'ζ':>8} {'f0err%':>7}"
           f"  {'errRMS_dB':>10} {'K_opt_dB':>9}"
           f"  {'Ru':>10} {'Rbp':>10} {'Rf':>10} {'Rf?':>3}"
           f"  {'a=Rf/Ru':>8} {'b=Rf/Rbp':>9}")
    print(hdr); print(sep)

    _C2_TAG = {'S': ' ', 'P': 'P', 'E': 'E'}
    for i, r in enumerate(results):
        ba = r['best_adder']
        c2_tag = _C2_TAG.get(r['C2_type'], ' ')
        print(f"{i+1:<3} {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>9}{c2_tag}"
              f" {fmt_res(r['R1']):>10} {fmt_res(r['R2_act']):>11}"
              f"  {r['f0']:>7.3f} {r['zeta']:>8.2f} {r['f0_err']:>6.2f}%"
              f"  {r['err_rms']:>9.4f}dB {r['K_opt_dB']:>+9.2f}dB"
              f"  {fmt_res(ba['Ru']):>10} {fmt_res(ba['Rbp']):>10}"
              f" {fmt_res(ba['Rf']):>10} {ba['Rf_type']:>3}"
              f"  {ba['a_real']:>8.4f} {ba['b_real']:>9.4f}")

    print(f"\n  (P = C2 paralelo de dos caps, E = C2 serie de dos caps)")

    # ── T-redes R2 y Adder por candidato ────────────────────────────────
    print(); print(sep)
    print("  DETALLES POR CANDIDATO — T-redes R2 y opciones Adder")
    print(sep)
    for i, r in enumerate(results):
        c1_tag = '[E]' if r['C1'] > 100e-9 else '[C]'
        c2_tag = '[E]' if r['C2'] > 100e-9 else '[C]'
        if r['C2_type'] == 'P':
            c2_par = f"(P: {fmt_cap(r['C2_a'])}+{fmt_cap(r['C2_b'])})"
        elif r['C2_type'] == 'E':
            c2_par = f"(E: {fmt_cap(r['C2_a'])}||{fmt_cap(r['C2_b'])})"
        else:
            c2_par = ''
        print(f"\n  #{i+1}: R1={fmt_res(r['R1'])}  C1={fmt_cap(r['C1'])}{c1_tag}"
              f"  C2={fmt_cap(r['C2'])}{c2_tag}{c2_par}"
              f"  R2_ideal={fmt_res(r['R2_ideal'])}  ζ={r['zeta']:.1f}"
              f"  errRMS={r['err_rms']:.4f}dB  K_opt={r['K_opt_dB']:+.2f}dB")

        print("    — T-redes R2:")
        for j, t in enumerate(r['tred_list'][:5]):
            used = " ◄ USADO" if r['r2_type']=='T' and j==0 else ""
            print(f"      T{j+1}: Ra={fmt_res(t['Ra'])} Rb={fmt_res(t['Rb'])} Rc={fmt_res(t['Rc'])}"
                  f"  → R2={fmt_res(t['R2_eq'])}  (err {t['err']:.3f}%){used}")

        print("    — Opciones Adder (Ru, Rbp, Rf):")
        for j, ad in enumerate(r['adder_list']):
            tred_str = ''
            if ad['Rf_type']=='T' and 'Rf_tred' in ad:
                Ra,Rb,Rc = ad['Rf_tred']
                tred_str = f"  [T: Ra={fmt_res(Ra)} Rb={fmt_res(Rb)} Rc={fmt_res(Rc)}]"
            used = " ◄ MEJOR" if j==0 else ""
            print(f"      A{j+1}: Ru={fmt_res(ad['Ru'])} Rbp={fmt_res(ad['Rbp'])}"
                  f" Rf={fmt_res(ad['Rf'])} ({ad['Rf_type']}){tred_str}"
                  f"  a={ad['a_real']:.4f} b={ad['b_real']:.4f}"
                  f"  errRMS={ad['err_rms']:.4f}dB  K={ad['K_opt_dB']:+.2f}dB{used}")

    # ── CSV — ordenado por ζ, ζ en primera columna ──────────────────────
    csv_path = os.path.join(script_dir, f"compensador_resultados_{grupo_slug}.csv")
    results_csv = sorted(results, key=lambda x: x['zeta'])   # ordenar por ζ
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        # Columnas: ζ primero, luego BP completo, luego Adder completo
        header = [
            # ── Identificación / rendimiento ──────────────────────────────
            "zeta", "rank", "err_rms_dB", "K_opt_dB", "K_opt_lin",
            # ── BP: Circuito ──────────────────────────────────────────────
            "f0_Hz", "f0_err_pct",
            # ── BP: Capacitores ───────────────────────────────────────────
            "C1_F", "C2_F", "C2_type", "C2_a_F", "C2_b_F",
            # ── BP: Resistencias ──────────────────────────────────────────
            "R1_ohm", "R2_ideal_ohm", "R2_act_ohm", "R2_type", "R2_err_pct",
            "Rref_ohm",
            # ── Adder (Sumador) ───────────────────────────────────────────
            # Ru: brazo unitario,  Rbp: brazo BP,  Rf: realimentación
            # Rf_Ru = Rf/Ru (ganancia brazo unitario, ideal ≈ 1)
            # Rf_Rbp = Rf/Rbp (ganancia brazo BP, ideal ≈ 1; si << 1 el BP necesita atenuación)
            "Ru_ohm", "Rbp_ohm", "Rf_ohm", "Rf_type",
            "Rf_Ru", "Rf_Rbp",
        ]
        for j in range(1,6):
            header += [f"T{j}_Ra", f"T{j}_Rb", f"T{j}_Rc", f"T{j}_R2eq", f"T{j}_err_pct"]
        for j in range(1,6):
            header += [f"A{j}_Ru", f"A{j}_Rbp", f"A{j}_Rf", f"A{j}_Rf_type",
                       f"A{j}_Rf_Ru", f"A{j}_Rf_Rbp", f"A{j}_err_rms", f"A{j}_K_dB"]
        w.writerow(header)
        for i, r in enumerate(results_csv):
            ba = r['best_adder']
            row = [
                round(r['zeta'],4), i+1,
                round(r['err_rms'],6), round(r['K_opt_dB'],4), round(r['K_opt_lin'],6),
                r['f0'], round(r['f0_err'],4),
                r['C1'], r['C2'], r['C2_type'],
                r['C2_a'] or '', r['C2_b'] or '',
                r['R1'], r['R2_ideal'], r['R2_act'], r['r2_type'], round(r['r2_err'],4),
                r['Rref'],
                ba['Ru'], ba['Rbp'], ba['Rf'], ba['Rf_type'],
                round(ba['a_real'],6), round(ba['b_real'],6),
            ]
            tlist = r['tred_list']
            for j in range(5):
                if j < len(tlist):
                    t = tlist[j]
                    row += [t['Ra'],t['Rb'],t['Rc'],round(t['R2_eq'],2),round(t['err'],4)]
                else:
                    row += ['','','','','']
            alist = r['adder_list']
            for j in range(5):
                if j < len(alist):
                    ad = alist[j]
                    row += [ad['Ru'],ad['Rbp'],ad['Rf'],ad['Rf_type'],
                            round(ad['a_real'],6),round(ad['b_real'],6),
                            round(ad['err_rms'],6),round(ad['K_opt_dB'],4)]
                else:
                    row += ['','','','','','','','']
            w.writerow(row)
    print(f"\n  CSV guardado: {csv_path}")

    # ── Gráfica ──────────────────────────────────────────────────────────
    N_PLOT   = min(5, len(results))
    H_id_plt = H_ideal_arr(freqs_plot)
    mag_id   = 20*np.log10(np.abs(H_id_plt))

    fig, (ax_mag, ax_err) = plt.subplots(2,1, figsize=(15,10), sharex=True)
    ax_mag.semilogx(freqs_plot, mag_id, color='black', ls='--', lw=2.5,
                    label='H_ideal (referencia)')

    for i in range(N_PLOT):
        r   = results[i]
        col = colores[i % len(colores)]
        ba  = r['best_adder']
        R1, R2, C1, C2 = r['R1'], r['R2_act'], r['C1'], r['C2']
        Ru, Rbp, Rf    = ba['Ru'], ba['Rbp'], ba['Rf']

        H_tot   = H_total_arr(R1, R2, C1, C2, Ru, Rbp, Rf, freqs_plot)
        mag_tot = 20*np.log10(np.abs(H_tot))
        mag_K   = mag_tot + r['K_opt_dB']   # K·|H_total| en dB
        err_f   = mag_K - mag_id

        lbl = (f"#{i+1} ζ={r['zeta']:.0f}"
               f" R1={fmt_res(R1)} C1={fmt_cap(C1)} C2={fmt_cap(C2)}"
               f" Ru={fmt_res(Ru)} Rbp={fmt_res(Rbp)} Rf={fmt_res(Rf)}"
               f" K={r['K_opt_dB']:+.1f}dB errRMS={r['err_rms']:.2f}dB")

        ax_mag.semilogx(freqs_plot, mag_K,  color=col, ls='-', lw=1.8, label=lbl)
        ax_err.semilogx(freqs_plot, err_f,  color=col, lw=1.5,
                        label=f"#{i+1} ζ={r['zeta']:.0f} errRMS={r['err_rms']:.2f}dB")

    ax_mag.axvline(f0_des, color='gray', ls=':', lw=1.0, label=f'f0={f0_des}Hz')
    ax_mag.set_ylabel('Magnitud (dB)')
    ax_mag.set_title(
        f'[{grupo_label}]  K·|H_total| (—) vs H_ideal (- -)  |  '
        f'ζ_geo={ZETA_GEO}  ζ_target={ZETA_TARGET}  f0={f0_des}Hz\n'
        f'H_ideal = (s²+2ζ₀ω₀s+ω₀²)/(s²+2ζ₁ω₀s+ω₀²)'
    )
    ax_mag.legend(loc='lower right', fontsize=6)
    ax_mag.grid(True, which='both', ls=':', alpha=0.4)
    ax_mag.set_xlim([freqs_plot[0], freqs_plot[-1]])

    ax_err.axhline(0,      color='black', ls='-',  lw=0.8)
    ax_err.axvline(f0_des, color='gray',  ls=':',  lw=1.0)
    ax_err.set_xlabel('Frecuencia (Hz)')
    ax_err.set_ylabel('Error (dB) = 20·log|K·H_total / H_ideal|')
    ax_err.set_title(f'[{grupo_label}]  Error sistema completo vs H_ideal')
    ax_err.legend(fontsize=8)
    ax_err.grid(True, which='both', ls=':', alpha=0.4)

    plt.tight_layout()
    plt.savefig(os.path.join(script_dir, f"compensador_plot_{grupo_slug}.png"), dpi=150)
    plt.show()
