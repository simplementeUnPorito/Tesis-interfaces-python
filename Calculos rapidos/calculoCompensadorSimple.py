# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import warnings
warnings.filterwarnings('ignore')

# ── Valores comerciales del kit ────────────────────────────────────────
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

def cap_comercial(v):
    return CAP_COMERCIALES[np.argmin([abs(c-v) for c in CAP_COMERCIALES])]

def res_comercial(v):
    return RES_COMERCIALES[np.argmin([abs(r-v) for r in RES_COMERCIALES])]

def fmt_cap(c):
    if c >= 1e-6: return f"{c*1e6:.2f}uF"
    if c >= 1e-9: return f"{c*1e9:.2f}nF"
    return f"{c*1e12:.2f}pF"

def fmt_res(r):
    if r >= 1e6: return f"{r/1e6:.2f}Mohm"
    if r >= 1e3: return f"{r/1e3:.2f}kohm"
    return f"{r:.1f}ohm"

# ════════════════════════════════════════════════════════════
# PARÁMETROS — editá acá
# ════════════════════════════════════════════════════════════
f0_des        = 10       # Hz — frecuencia natural del geófono
gamma0        = 0.25     # damping inicial del geófono (γ₀)
TOP_N         = 15       # resultados a mostrar
lambda_cost   = 1        # peso del costo de componentes
SOLO_CERAMICOS = False   # True → restringe C1,C2 ≤ C_max_timing (sin electrolíticos)

# Impedancias del op-amp: R1 y red T DEBEN estar en este rango.
R_OUT_OPAMP = 20       # Ω — impedancia de salida
R_IN_OPAMP  = 35e6    # Ω — impedancia de entrada (en paralelo con R2 en nodo inversor)
# ════════════════════════════════════════════════════════════

omega0       = 2*np.pi*f0_des
R_min_val    = 1e3
R_max_val    = 1e6
C_max_timing = 100e-9   # límite cerámicos

def in_range(R):
    return R_min_val <= R <= R_max_val

def cap_cost(C_list):
    """Costo por caps > 100nF (preferencia cerámica)."""
    cost = 0
    for C in C_list:
        if C > 100e-9:
            cost += 20 * np.log10(C / 100e-9)**2
    return cost

# ══════════════════════════════════════════════════════════════════════
# ζ_MAX ANALÍTICO
#   Con R2_ideal → ∞, R2_eff → R_IN_OPAMP.
#   Para f0=f0_des: τ1*τ2_lim = 1/ω0²  con τ2_lim = R_IN_OPAMP*C2
#   → τ1 = 1/(ω0²*R_IN_OPAMP*C2)  → R1 = τ1/C1
#   ζ_lim = (τ1 + τ2_lim) / (2*√(τ1*τ2_lim))
# ══════════════════════════════════════════════════════════════════════

def compute_zeta_max():
    """
    Calcula el ζ máximo alcanzable con f0=f0_des, usando:
    - Caps cerámicos ≤ C_max_timing (sin electrolíticos en la red RC)
    - R2_eff limitada por el T-net: R2_eff_max = R2_max_tred ∥ R_IN_OPAMP
    La τ2 límite usa R2_eff_max, no R_IN_OPAMP puro.
    """
    # R2_max_tred todavía no está definido aquí, lo calculamos localmente
    Ra_max   = max(r for r in RES_COMERCIALES if R_min_val <= r <= R_max_val)
    Rc_min   = min(r for r in RES_COMERCIALES if R_min_val <= r <= R_max_val)
    R2t_max  = 2*Ra_max + Ra_max**2/Rc_min
    R2eff_max = (R2t_max * R_IN_OPAMP) / (R2t_max + R_IN_OPAMP)

    caps = [c for c in CAP_COMERCIALES if 1e-9 <= c <= C_max_timing]
    best_zeta = 0.0
    best = None
    for C1 in caps:
        for C2 in caps:
            tau2_lim  = R2eff_max * C2
            tau1_need = 1.0 / (omega0**2 * tau2_lim)
            R1_ideal  = tau1_need / C1
            if not in_range(R1_ideal): continue
            zeta = (tau1_need + tau2_lim) / (2.0 * np.sqrt(tau1_need * tau2_lim))
            if zeta > best_zeta:
                best_zeta = zeta
                best = dict(R1_ideal=R1_ideal, C1=C1, C2=C2, zeta=zeta,
                            tau1=tau1_need, tau2=tau2_lim,
                            R2eff_max=R2eff_max,
                            f0_lim=1.0/(2*np.pi*np.sqrt(tau1_need*tau2_lim)))
    return best_zeta, best

gamma1, _zp = compute_zeta_max()
target_num  = 2 * (gamma1 - gamma0) * omega0

# ══════════════════════════════════════════════════════════════════════
# CIRCUITO (con R2_eff = R2_ideal ∥ R_IN_OPAMP)
# H(s) = -s·R2_eff·C1 / ((sR1C1+1)(sR2_eff·C2+1))
#
# τ1=R1C1, τ2_eff=R2_eff·C2
# ζ = (τ1+τ2_eff)/(2√(τ1·τ2_eff))
# ω0 = 1/√(τ1·τ2_eff)
# num_coeff = 1/(R1·C2)   [independiente de R2_eff]
# ══════════════════════════════════════════════════════════════════════

def verificar(R1, R2_ideal, C1, C2):
    """Verifica con R2_eff = R2_ideal ∥ R_IN_OPAMP."""
    if R1 <= 0 or R2_ideal <= 0 or C1 <= 0 or C2 <= 0:
        return None, None, None, None
    R2_eff = (R2_ideal * R_IN_OPAMP) / (R2_ideal + R_IN_OPAMP)
    tau1 = R1 * C1
    tau2 = R2_eff * C2
    if tau1 <= 0 or tau2 <= 0: return None, None, None, None
    w0   = 1.0 / np.sqrt(tau1 * tau2)
    zeta = (tau1 + tau2) / (2.0 * np.sqrt(tau1 * tau2))
    return w0 / (2*np.pi), zeta, 1.0 / (R1 * C2), R2_eff

# ══════════════════════════════════════════════════════════════════════
# RED T PARA R2
#   Ra — nodo M — Rb   R2_eq = Ra + Rb + Ra·Rb/Rc
#          |
#          Rc
#          |
#         GND
#
# TODOS Ra, Rb, Rc deben estar en [R_min_val, R_max_val].
# ══════════════════════════════════════════════════════════════════════

_RES_EN_RANGO = [r for r in RES_COMERCIALES if R_min_val <= r <= R_max_val]

def res_en_rango_mas_cercano(v):
    """Snapea al comercial más cercano QUE ESTÉ dentro del rango."""
    return _RES_EN_RANGO[np.argmin([abs(r - v) for r in _RES_EN_RANGO])]

def buscar_tred(R2_target, lam=1, top_n=5):
    """
    Sweep Ra×Rb (ambos en rango) → Rc analítico, snapeado al comercial EN RANGO.
    TODOS Ra, Rb, Rc deben quedar dentro de [R_min_val, R_max_val].
    """
    resultados = {}
    for Ra in _RES_EN_RANGO:
        for Rb in _RES_EN_RANGO:
            denom = R2_target - Ra - Rb
            if denom <= 0: continue
            Rc_i = Ra * Rb / denom
            if Rc_i <= 0: continue
            Rc    = res_en_rango_mas_cercano(Rc_i)
            R2_eq = Ra + Rb + Ra * Rb / Rc
            err   = abs(R2_eq - R2_target) / R2_target * 100
            key   = (Ra, Rb, Rc)
            if key not in resultados or err < resultados[key]['err']:
                resultados[key] = dict(Ra=Ra, Rb=Rb, Rc=Rc, Rc_ideal=Rc_i,
                                       R2_eq=R2_eq, err=err)
    return sorted(resultados.values(), key=lambda x: x['err'])[:top_n]

# ══════════════════════════════════════════════════════════════════════
# RED T DE CAPACITORES PARA C2
#   C_eff = Ca·Cb / (Ca + Cb + Cc)
#   Topología: R2 ──Ca──●──Cb── nodo inversor (virtual GND)
#                        |
#                       Cc
#                        |
#                       GND
#   Cc analítico: Cc = Ca·Cb/C_eff − Ca − Cb
# ══════════════════════════════════════════════════════════════════════

def buscar_ctred_cap(C_target, top_n=5):
    """
    Sintetiza C_target con red T de caps: C_eff = Ca·Cb/(Ca+Cb+Cc).
    Sweep Ca×Cb (ambos ≥ C_target), Cc analítico → snapeado al comercial.
    """
    resultados = {}
    caps = [c for c in CAP_COMERCIALES if c >= C_target]
    for Ca in caps:
        for Cb in caps:
            Cc_ideal = Ca * Cb / C_target - Ca - Cb
            if Cc_ideal <= 0: continue
            Cc    = cap_comercial(Cc_ideal)
            C_eff = Ca * Cb / (Ca + Cb + Cc)
            if C_eff <= 0: continue
            err   = abs(C_eff - C_target) / C_target * 100
            key   = tuple(sorted([Ca, Cb]) + [Cc])
            if key not in resultados or err < resultados[key]['err']:
                resultados[key] = dict(Ca=Ca, Cb=Cb, Cc=Cc, Cc_ideal=Cc_ideal,
                                       C_eff=C_eff, err=err)
    return sorted(resultados.values(), key=lambda x: x['err'])[:top_n]

def buscar_cpar(C_target, top_n=5):
    """Sintetiza C_target como par en paralelo: C_eff = Ca + Cb."""
    resultados = {}
    caps = [c for c in CAP_COMERCIALES if c <= C_target]
    for Ca in caps:
        Cb_ideal = C_target - Ca
        if Cb_ideal <= 0: continue
        Cb    = cap_comercial(Cb_ideal)
        C_eff = Ca + Cb
        err   = abs(C_eff - C_target) / C_target * 100
        key   = tuple(sorted([Ca, Cb]))
        if key not in resultados or err < resultados[key]['err']:
            resultados[key] = dict(Ca=Ca, Cb=Cb, C_eff=C_eff, err=err)
    return sorted(resultados.values(), key=lambda x: x['err'])[:top_n]

# Precomputar valores de C2 sintetizables con red T de caps (cerámicos ≥ 1nF)
# Fórmula: C_eff = Ca·Cb/(Ca+Cb+Cc) con Ca,Cb,Cc comerciales ≥ 1nF
def _gen_c2_ctred_map():
    nf_caps  = [c for c in CAP_COMERCIALES if 1e-9 <= c <= C_max_timing]
    c2_map   = {}  # C_eff_rounded → best (Ca,Cb,Cc,C_eff,err)
    for Ca in nf_caps:
        for Cb in nf_caps:
            for Cc in nf_caps:
                C_eff = Ca * Cb / (Ca + Cb + Cc)
                if C_eff < 1e-12: continue
                key = float(f"{C_eff:.3e}")
                if key not in c2_map:
                    c2_map[key] = dict(Ca=Ca, Cb=Cb, Cc=Cc, C_eff=C_eff)
    return c2_map

_C2_CTRED_MAP = _gen_c2_ctred_map()

# ══════════════════════════════════════════════════════════════════════
# GRUPOS DE BÚSQUEDA
# R1: filtro DURO en [R_min_val, R_max_val]
# R2: libre (se implementa con red T) — se verifica con R2_ideal exacto
# ══════════════════════════════════════════════════════════════════════

if SOLO_CERAMICOS:
    _CAPS_BUSQ = [c for c in CAP_COMERCIALES if 1e-9 <= c <= C_max_timing]
else:
    _CAPS_BUSQ = [c for c in CAP_COMERCIALES if c >= 1e-9]

def grupo1_exacto():
    """C2 = r*C1 analítico (cuadrática), R1 y R2 analíticos."""
    caps = _CAPS_BUSQ
    res  = {}
    if gamma1 <= 1: return []
    sg = np.sqrt(gamma1**2 - 1)
    for C1 in caps:
        R2_i = target_num / (omega0**2 * C1)
        if R2_i <= 0: continue
        for sign in [+1, -1]:
            r    = omega0 * (gamma1 + sign * sg) / target_num
            C2_i = r * C1
            R1_i = 1.0 / (target_num * C2_i)
            if R1_i <= 0: continue
            C1_c = cap_comercial(C1);   C2_c = cap_comercial(C2_i)
            R1_c = res_comercial(R1_i)
            if not in_range(R1_c): continue
            f0c, zetac, numc, r2e = verificar(R1_c, R2_i, C1_c, C2_c)
            if f0c is None: continue
            ef = abs(f0c   - f0_des)    / f0_des    * 100
            ez = abs(zetac - gamma1)    / gamma1    * 100
            en = abs(numc  - target_num)/ target_num* 100
            cc = cap_cost([C1_c, C2_c])
            et = ef + ez + en + lambda_cost * cc
            e  = dict(C1=C1_c, C2=C2_c, C2_ideal=C2_i,
                      R1_ideal=R1_i, R2_ideal=R2_i, R1=R1_c, R2_eff=r2e,
                      f0=f0c, zeta=zetac, num=numc,
                      ef=ef, ez=ez, en=en, cc=cc, et=et)
            key = (C1_c, C2_c, R1_c)
            if key not in res or et < res[key]['et']: res[key] = e
    return sorted(res.values(), key=lambda x: x['et'])

def grupo2_sweep():
    """Sweep C1×C2, R1 y R2 analíticos."""
    caps = _CAPS_BUSQ
    res  = {}
    for C1 in caps:
        R2_i = target_num / (omega0**2 * C1)
        if R2_i <= 0: continue
        for C2 in caps:
            R1_i = 1.0 / (target_num * C2)
            if R1_i <= 0: continue
            C1_c = cap_comercial(C1);   C2_c = cap_comercial(C2)
            R1_c = res_comercial(R1_i)
            if not in_range(R1_c): continue
            f0c, zetac, numc, r2e = verificar(R1_c, R2_i, C1_c, C2_c)
            if f0c is None: continue
            ef = abs(f0c   - f0_des)    / f0_des    * 100
            ez = abs(zetac - gamma1)    / gamma1    * 100
            en = abs(numc  - target_num)/ target_num* 100
            cc = cap_cost([C1_c, C2_c])
            et = ef + ez + en + lambda_cost * cc
            e  = dict(C1=C1_c, C2=C2_c, R1_ideal=R1_i, R2_ideal=R2_i, R1=R1_c, R2_eff=r2e,
                      f0=f0c, zeta=zetac, num=numc,
                      ef=ef, ez=ez, en=en, cc=cc, et=et)
            key = (C1_c, C2_c, R1_c)
            if key not in res or et < res[key]['et']: res[key] = e
    return sorted(res.values(), key=lambda x: x['et'])

def grupo3_bruto():
    """Sweep R1×C1×C2 con R2 analítico de ω0²."""
    caps = _CAPS_BUSQ
    res  = {}
    for R1 in RES_COMERCIALES:
        if not in_range(R1): continue
        for C1 in caps:
            for C2 in caps:
                R2_i = 1.0 / (omega0**2 * R1 * C1 * C2)
                if R2_i <= 0: continue
                f0c, zetac, numc, r2e = verificar(R1, R2_i, C1, C2)
                if f0c is None: continue
                ef = abs(f0c   - f0_des)    / f0_des    * 100
                ez = abs(zetac - gamma1)    / gamma1    * 100
                en = abs(numc  - target_num)/ target_num* 100
                cc = cap_cost([C1, C2])
                et = ef + ez + en + lambda_cost * cc
                e  = dict(C1=C1, C2=C2, R1=R1, R2_ideal=R2_i, R2_eff=r2e,
                          f0=f0c, zeta=zetac, num=numc,
                          ef=ef, ez=ez, en=en, cc=cc, et=et)
                key = (C1, C2, R1)
                if key not in res or et < res[key]['et']: res[key] = e
    return sorted(res.values(), key=lambda x: x['et'])

def grupo5_ctred_c2():
    """
    Sweep C1 × C2_sintetizado (red T de caps: Ca·Cb/(Ca+Cb+Cc)).
    Permite obtener C2_eff muy pequeño con cerámicos → R1 y R2 en rango normal.
    """
    caps = _CAPS_BUSQ
    res  = {}
    for C1 in caps:
        R2_i = target_num / (omega0**2 * C1)
        if R2_i <= 0: continue
        for C2_eff, ctred_info in _C2_CTRED_MAP.items():
            R1_i = 1.0 / (target_num * C2_eff)
            if R1_i <= 0: continue
            C1_c = cap_comercial(C1)
            R1_c = res_comercial(R1_i)
            if not in_range(R1_c): continue
            f0c, zetac, numc, r2e = verificar(R1_c, R2_i, C1_c, C2_eff)
            if f0c is None: continue
            ef = abs(f0c   - f0_des)    / f0_des    * 100
            ez = abs(zetac - gamma1)    / gamma1    * 100
            en = abs(numc  - target_num)/ target_num* 100
            cc = cap_cost([C1_c])       # solo C1 (C2 es red T de cerámicos)
            et = ef + ez + en + lambda_cost * cc
            e  = dict(C1=C1_c, C2=C2_eff, R1_ideal=R1_i, R2_ideal=R2_i,
                      R1=R1_c, R2_eff=r2e, f0=f0c, zeta=zetac, num=numc,
                      ef=ef, ez=ez, en=en, cc=cc, et=et, ctred_c2=ctred_info)
            key = (C1_c, round(C2_eff, 20), R1_c)
            if key not in res or et < res[key]['et']: res[key] = e
    return sorted(res.values(), key=lambda x: x['et'])

# ══════════════════════════════════════════════════════════════════════
# OUTPUT
# ══════════════════════════════════════════════════════════════════════
if gamma1 > 1:
    _sg  = np.sqrt(gamma1**2 - 1)
    tau_l = (gamma1 + _sg) / omega0
    tau_r = (gamma1 - _sg) / omega0
else:
    tau_l = tau_r = 1.0/omega0

_Ra_max     = max(_RES_EN_RANGO)
_Rc_min     = min(_RES_EN_RANGO)
R2_max_tred = 2*_Ra_max + _Ra_max**2/_Rc_min

print("="*92)
print("  COMPENSADOR RC + RED T — H(s) = -s·R2_eff·C1 / ((sR1C1+1)(sR2_eff·C2+1))")
print(f"  f0={f0_des}Hz  g0={gamma0}  w0={omega0:.3f}rad/s")
print(f"  R_IN_OPAMP={fmt_res(R_IN_OPAMP)}  (en paralelo con R2 → R2_eff = R2_ideal ∥ Rin)")
print()
print(f"  ζ_max teórico (f0={f0_des}Hz, R2_ideal→∞): {gamma1:.2f}")
if _zp:
    print(f"    → C1={fmt_cap(_zp['C1'])}  C2={fmt_cap(_zp['C2'])}"
          f"  R1_ideal={fmt_res(_zp['R1_ideal'])}"
          f"  R2_eff_max={fmt_res(_zp['R2eff_max'])} (T-net∥Rin)")
print(f"  Target auto: gamma1={gamma1:.2f}  target_num={target_num:.2f}")
print(f"  Target: -{target_num:.2f}·s / (s²+{2*gamma1*omega0:.2f}·s+{omega0**2:.4f})")
print(f"  τ_lento={tau_l:.4f}s  τ_rapido={tau_r*1e6:.2f}μs")
print(f"  R1 y T-red: rango DURO [{fmt_res(R_min_val)}, {fmt_res(R_max_val)}]")
print(f"  R2 libre — verificado con R2_ideal exacto")
print(f"  R2_max implementable con T-red: {fmt_res(R2_max_tred)}"
      f"  (Ra=Rb={fmt_res(_Ra_max)}, Rc={fmt_res(_Rc_min)})")
print("="*92)

SEP = "-"*92

def ajustar_con_tred(tops, max_candidatos=150):
    """Re-verifica los mejores candidatos con R2_eq de la red T."""
    tred_cache = {}
    resultado  = []
    seen       = set()
    for r in tops:
        if len(seen) >= max_candidatos: break
        key = (r['C1'], r['C2'], r['R1'])
        if key in seen: continue
        seen.add(key)
        R2_k = float(f"{r['R2_ideal']:.4e}")
        if R2_k not in tred_cache:
            trs = buscar_tred(r['R2_ideal'], top_n=1)
            tred_cache[R2_k] = trs[0] if trs else None
        t = tred_cache[R2_k]
        if t is None: continue
        f0t, zt, nt, r2et = verificar(r['R1'], t['R2_eq'], r['C1'], r['C2'])
        if f0t is None: continue
        ef = abs(f0t - f0_des)    / f0_des    * 100
        ez = abs(zt  - gamma1)    / gamma1    * 100
        en = abs(nt  - target_num)/ target_num* 100
        cc = cap_cost([r['C1'], r['C2']])
        et = ef + ez + en + lambda_cost * cc
        resultado.append({**r, 'tred': t, 'R2_eff_tred': r2et,
                          'f0r': f0t, 'zetar': zt, 'numr': nt,
                          'efr': ef, 'ezr': ez, 'enr': en, 'etr': et})
    return sorted(resultado, key=lambda x: x['etr'])

if SOLO_CERAMICOS:
    print(f"\n  [SOLO_CERAMICOS] Caps restringidas a ≤ {fmt_cap(C_max_timing)}")
    print(f"  [SOLO_CERAMICOS] ζ_max con cerámicos ≈ {gamma1:.2f}"
          f"  (puede ser muy bajo — ajustar target manualmente si es necesario)")

print(f"\n  Calculando grupos...")
tops3_raw = grupo3_bruto()
todos_g5  = grupo5_ctred_c2()
todos_raw = grupo1_exacto() + grupo2_sweep() + tops3_raw + todos_g5

# Deduplicar y tomar los 200 mejores por error ideal
_seen_pre = set()
todos = []
for r in sorted(todos_raw, key=lambda x: x['et']):
    k = (r['C1'], r['C2'], r['R1'])
    if k not in _seen_pre:
        _seen_pre.add(k)
        todos.append(r)
    if len(todos) >= 200: break

# ── Tabla 1: ranking por error ideal ─────────────────────────────────
print(f"\n{SEP}")
print("  RANKING POR ERROR IDEAL  (R2_eff=R2_ideal∥Rin — antes de cuantizar T-red)")
print(SEP)
print(f"{'#':<3} {'C1':>8} {'C2':>8} {'R1':>10} {'R2_ideal':>11} {'R2_eff':>11}"
      f"  {'f0':>7} {'ζ':>7} {'eZ%':>6} {'eN%':>6} {'err%':>7}  T-red?")
print(SEP)
_tred_cache_pre = {}
for i, r in enumerate(todos[:TOP_N]):
    R2_k = float(f"{r['R2_ideal']:.4e}")
    if R2_k not in _tred_cache_pre:
        trs = buscar_tred(r['R2_ideal'], top_n=1)
        _tred_cache_pre[R2_k] = trs[0] if trs else None
    t     = _tred_cache_pre[R2_k]
    t_tag = f"{t['err']:4.1f}%✓" if t else "  ✗"
    r2e   = r.get('R2_eff', float('nan'))
    print(f"{i+1:<3} {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>8}"
          f" {fmt_res(r['R1']):>10} {fmt_res(r['R2_ideal']):>11} {fmt_res(r2e):>11}"
          f"  {r['f0']:>7.3f} {r['zeta']:>7.2f}"
          f" {r['ez']:>6.2f} {r['en']:>6.2f} {r['et']:>7.2f}  {t_tag}")

# ── Tabla 2: re-rankeado con R2_eq real ──────────────────────────────
print(f"\n  Ajustando con red T (re-rank)...")
tops_tred = ajustar_con_tred(todos)

print(f"\n{SEP}")
print("  RANKING CON RED T  (re-rankeado por error real, R2_eff=R2_eq∥Rin)")
print(SEP)
print(f"{'#':<3} {'C1':>8} {'C2':>8} {'R1':>10} {'R2_eq':>11} {'R2_eff':>11}"
      f"  {'f0':>7} {'ζ':>7} {'eZ%':>6} {'eN%':>6} {'err%':>7}  T-red err")
print(SEP)
for i, r in enumerate(tops_tred[:TOP_N]):
    t   = r['tred']
    r2e = r.get('R2_eff_tred', float('nan'))
    print(f"{i+1:<3} {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>8}"
          f" {fmt_res(r['R1']):>10} {fmt_res(t['R2_eq']):>11} {fmt_res(r2e):>11}"
          f"  {r['f0r']:>7.3f} {r['zetar']:>7.2f}"
          f" {r['ezr']:>6.2f} {r['enr']:>6.2f} {r['etr']:>7.2f}  {t['err']:5.2f}%")

if tops_tred:
    b   = tops_tred[0]
    t   = b['tred']
    r2e = b.get('R2_eff_tred', float('nan'))
    print(f"\n  MEJOR BUILDABLE: R1={fmt_res(b['R1'])}  C1={fmt_cap(b['C1'])}  C2={fmt_cap(b['C2'])}")
    print(f"    Ra={fmt_res(t['Ra'])}  Rb={fmt_res(t['Rb'])}  Rc={fmt_res(t['Rc'])}"
          f"  →  R2_eq={fmt_res(t['R2_eq'])}  R2_eff={fmt_res(r2e)}")
    print(f"       (R2_ideal={fmt_res(b['R2_ideal'])}, Tred err {t['err']:.2f}%,"
          f" Rin carga {abs(1-r2e/t['R2_eq'])*100:.1f}%)")
    print(f"    f0={b['f0r']:.4f}Hz (err {b['efr']:.2f}%)  |"
          f"  ζ={b['zetar']:.3f} (err {b['ezr']:.2f}%)  |"
          f"  num={b['numr']:.1f} (err {b['enr']:.2f}%)")

    # ── R_bleed: resistencia de polarización DC en paralelo con C1 ────
    # El polo HP = 1/(2π·R_bleed·C1) debe estar en fc_low = f0·(γ-√(γ²-1))
    # (frecuencia de corte inferior del compensador — peor caso admisible).
    C1_best  = b['C1']
    fc_low   = f0_des * (gamma1 - np.sqrt(max(gamma1**2 - 1.0, 0.0)))
    R_bleed_ideal     = 1.0 / (2*np.pi*fc_low*C1_best)
    R_bleed_comercial = res_comercial(R_bleed_ideal)
    f_HP_real         = 1.0 / (2*np.pi*R_bleed_comercial*C1_best)
    print(f"\n  R_bleed (bias DC ∥ C1={fmt_cap(C1_best)}):")
    print(f"    fc_low = f0·(γ-√(γ²-1)) = {fc_low:.5f}Hz")
    print(f"    R_bleed_ideal = {fmt_res(R_bleed_ideal)}"
          f"  → comercial: {fmt_res(R_bleed_comercial)}")
    print(f"    f_polo_HP = {f_HP_real:.5f}Hz  (debe ≤ fc_low={fc_low:.5f}Hz)")

    # ── Síntesis de C1 con caps en paralelo (alternativa a electrolítico) ──
    C1_best = b['C1']
    if C1_best > C_max_timing:
        pares = buscar_cpar(C1_best, top_n=3)
        print(f"\n  C1={fmt_cap(C1_best)} → síntesis en paralelo (reemplaza electrolítico):")
        for p in pares:
            print(f"    {fmt_cap(p['Ca'])} ∥ {fmt_cap(p['Cb'])} = {fmt_cap(p['C_eff'])}"
                  f"  (err {p['err']:.2f}%)")

    # ── Síntesis de C2 con red T de caps ──────────────────────────────
    C2_best = b['C2']
    ctred   = buscar_ctred_cap(C2_best, top_n=3)
    if ctred:
        print(f"\n  C2={fmt_cap(C2_best)} → red T de caps (Ca·Cb/(Ca+Cb+Cc)):")
        for tc in ctred:
            print(f"    Ca={fmt_cap(tc['Ca'])}  Cb={fmt_cap(tc['Cb'])}"
                  f"  Cc={fmt_cap(tc['Cc'])}  → C_eff={fmt_cap(tc['C_eff'])}"
                  f"  (err {tc['err']:.2f}%)")

    # ── Mejor solución de grupo5 (C2 vía red T, sin red T de resistores) ─
    tops5 = [r for r in tops_tred if 'ctred_c2' in r]
    if tops5:
        b5 = tops5[0]
        tc = b5['ctred_c2']
        t5 = b5['tred']
        print(f"\n  MEJOR CON C2 VÍA RED T (sin red T de resistores si R2 en rango):")
        print(f"    R1={fmt_res(b5['R1'])}  C1={fmt_cap(b5['C1'])}"
              f"  C2_eff={fmt_cap(b5['C2'])}  R2={fmt_res(t5['R2_eq'])}")
        print(f"    C2 red T: Ca={fmt_cap(tc['Ca'])} Cb={fmt_cap(tc['Cb'])}"
              f" Cc={fmt_cap(tc['Cc'])}")
        print(f"    f0={b5['f0r']:.4f}Hz  ζ={b5['zetar']:.3f}"
              f"  (err ζ {b5['ezr']:.2f}%  err f0 {b5['efr']:.2f}%)")

tops1 = tops_tred
tops2 = []
tops3 = []

# ════════════════════════════════════════════════════════════
# GRAFICACIÓN
# ════════════════════════════════════════════════════════════
GRAFICAR = {
    'grupo1': [1],
    'grupo2': [],
    'grupo3': [],
}

import matplotlib.pyplot as plt
from scipy.signal import TransferFunction, bode as scipy_bode

def bode_RC(R1, R2_ideal, C1, C2, freqs_hz):
    """Calcula respuesta en frecuencia usando R2_eff = R2_ideal ∥ Rin."""
    R2_eff = (R2_ideal * R_IN_OPAMP) / (R2_ideal + R_IN_OPAMP)
    num = [-R2_eff*C1, 0]
    den = [R1*C1*R2_eff*C2, R1*C1+R2_eff*C2, 1]
    _, mag, _ = scipy_bode(TransferFunction(num, den), w=2*np.pi*freqs_hz)
    return mag

freqs = np.logspace(np.log10(f0_des/10000), np.log10(f0_des*10000), 4000)

any_sel = any(len(v) > 0 for v in GRAFICAR.values())
if any_sel:
    num_id = [-target_num, 0]
    den_id = [1, 2*gamma1*omega0, omega0**2]
    _, mag_id, _ = scipy_bode(TransferFunction(num_id, den_id), w=2*np.pi*freqs)

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.semilogx(freqs, mag_id, 'k-', lw=2.5, zorder=10,
                label=f'Ideal  g0={gamma0} g1={gamma1:.1f} f0={f0_des}Hz')

    paletas = {1:['royalblue','cornflowerblue','steelblue'],
               2:['forestgreen','mediumseagreen','limegreen'],
               3:['firebrick','tomato','salmon']}

    for gnum, tops in [(1,tops1),(2,tops2),(3,tops3)]:
        indices = GRAFICAR.get(f'grupo{gnum}',[])
        if not indices or not tops: continue
        colores = paletas[gnum]
        for ii, idx in enumerate(indices):
            r = tops[idx-1] if 0 < idx <= len(tops) else None
            if r is None: continue
            col = colores[ii % 3]
            mag = bode_RC(r['R1'], r['R2_ideal'], r['C1'], r['C2'], freqs)
            lbl = (f"G{gnum}#{idx} R1={fmt_res(r['R1'])}"
                   f" C1={fmt_cap(r['C1'])} C2={fmt_cap(r['C2'])}"
                   f" R2={fmt_res(r['R2_ideal'])} ζ={r['zeta']:.1f}")
            ax.semilogx(freqs, mag, color=col, lw=1.8, label=lbl)
            if 'tred' in r:
                t     = r['tred']
                mag_t = bode_RC(r['R1'], t['R2_eq'], r['C1'], r['C2'], freqs)
                ax.semilogx(freqs, mag_t, color=col, lw=1.0, ls='--',
                            label=f"  con T-red R2={fmt_res(t['R2_eq'])}")

    ax.axvline(f0_des, color='gray', ls=':', lw=1)
    ax.set_xlabel('Frecuencia (Hz)'); ax.set_ylabel('Magnitud (dB)')
    ax.set_title(f'H(s)  |  f0={f0_des}Hz  ζ_max={gamma1:.1f}  Rin={fmt_res(R_IN_OPAMP)}')
    ax.grid(True, which='both', ls=':', alpha=0.5)
    ax.legend(loc='lower right', fontsize=7)
    plt.tight_layout(); plt.show()
