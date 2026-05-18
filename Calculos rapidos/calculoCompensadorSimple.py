# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import warnings
import csv
import os
warnings.filterwarnings('ignore')

# ════════════════════════════════════════════════════════════
# PARÁMETROS
# ════════════════════════════════════════════════════════════
f0_des     = 10       # Hz — frecuencia natural del geófono
ZETA_MIN   = 100      # ζ mínimo buscado
ZETA_MAX   = 1000     # ζ máximo buscado
TOP_N      = 15       # candidatos a mostrar
F0_TOL_PCT = 10.0     # % error máximo en f0 aceptable

# Op-amp:
A0_dB    = 90                    # dB ganancia DC lazo abierto
A0       = 10**(A0_dB / 20.0)   # lineal ≈ 31623
f_p      = 8e6                   # Hz — polo dominante
omega_p  = 2*np.pi * f_p
Rin      = 35e6                  # Ω — resistencia entrada en nodo inversor

omega0 = 2*np.pi * f0_des

# Geófono:
ZETA_GEO    = 0.25    # amortiguamiento natural del geófono
ZETA_TARGET = 1000    # amortiguamiento objetivo de compensación

# ── Valores comerciales ──────────────────────────────────────────────────
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

R_min_val = 1e3
R_max_val = 1e6
_RES_EN_RANGO = [r for r in RES_COMERCIALES if R_min_val <= r <= R_max_val]
_Ra_max = max(_RES_EN_RANGO)
_Rc_min = min(_RES_EN_RANGO)
R2_max_tred = 2*_Ra_max + _Ra_max**2/_Rc_min

CAPS_TODOS     = list(CAP_COMERCIALES)                         # todos (pF … 1000µF)
CAPS_CERAMICOS = [c for c in CAP_COMERCIALES if c <= 100e-9]  # solo cerámicos (≤ 100nF)

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

# ════════════════════════════════════════════════════════════
# FUNCIONES DE TRANSFERENCIA
#
# TF circuito real (A0 finito, polo ωp, carga Rin):
#
#              -β(s)
#  Av(s) = ──────────────────────────────────────────────────
#           1 + ((1+s/ωp)/A0) * (1 + β(s) + (R2/Rin)/(sR2C2+1))
#
#  donde  β(s) = sR2C1 / ((sR1C1+1)(sR2C2+1))
#
# TF deseada del compensador (referencia de diseño):
#  H_des(s) = -2·(ζ_target - ζ_geo)·ω0·s / (s² + 2·ζ_target·ω0·s + ω0²)
# ════════════════════════════════════════════════════════════

def H_real_arr(R1, R2, C1, C2, freqs_hz):
    s = 1j * 2*np.pi * freqs_hz
    beta   = (s * R2 * C1) / ((s*R1*C1 + 1) * (s*R2*C2 + 1))
    z_load = (R2 / Rin) / (s*R2*C2 + 1)
    nif    = (1 + s/omega_p) / A0
    return -beta / (1 + nif * (1 + beta + z_load))

def H_deseada_arr(freqs_hz):
    s = 1j * 2*np.pi * freqs_hz
    num = -2 * (ZETA_TARGET - ZETA_GEO) * omega0 * s
    den = s**2 + 2*ZETA_TARGET*omega0*s + omega0**2
    return num / den

# Frecuencias de evaluación del error: 3 décadas bajo f0 hasta 100 kHz
FREQS_EVAL      = np.logspace(np.log10(f0_des/1000), np.log10(1e5), 800)
_H_DESEADA_EVAL = H_deseada_arr(FREQS_EVAL)   # precomputada — no depende de R,C

def rms_error_dB(R1, R2, C1, C2):
    Hr = np.abs(H_real_arr(R1, R2, C1, C2, FREQS_EVAL))
    Hd = np.abs(_H_DESEADA_EVAL)
    mask = (Hr > 1e-30) & (Hd > 1e-30)
    if not np.any(mask): return np.inf
    err = 20*np.log10(Hr[mask]) - 20*np.log10(Hd[mask])
    return float(np.sqrt(np.mean(err**2)))

def circuito_params(R1, R2, C1, C2):
    """f0 y ζ usando la aproximación τ2 = R2_eff*C2 (R2_eff = R2||Rin)."""
    R2_eff = R2 * Rin / (R2 + Rin)
    tau1 = R1 * C1
    tau2 = R2_eff * C2
    if tau1 <= 0 or tau2 <= 0: return None, None
    w0   = 1.0 / np.sqrt(tau1 * tau2)
    zeta = (tau1 + tau2) / (2*np.sqrt(tau1 * tau2))
    return w0 / (2*np.pi), zeta

# ════════════════════════════════════════════════════════════
# T-REDES DE RESISTENCIAS
#   Topología: Vout ──Ra── M ──Rb── V_minus
#                            |
#                            Rc
#                            |
#                           GND
#   R2_eq = Ra + Rb + Ra*Rb/Rc
# ════════════════════════════════════════════════════════════
print("  Precomputando tabla de T-redes R...", flush=True)
_tred_entries = []
for _Ra in _RES_EN_RANGO:
    for _Rb in _RES_EN_RANGO:
        for _Rc in _RES_EN_RANGO:
            _tred_entries.append((_Ra + _Rb + _Ra*_Rb/_Rc, _Ra, _Rb, _Rc))
_tred_entries.sort(key=lambda x: x[0])
_TRED_R2_ARR = np.array([e[0] for e in _tred_entries])

def mejores_tred(R2_target, top_n=5):
    idx0 = int(np.searchsorted(_TRED_R2_ARR, R2_target))
    window = 300
    lo = max(0, idx0 - window); hi = min(len(_tred_entries), idx0 + window)
    candidates = sorted(_tred_entries[lo:hi], key=lambda e: abs(e[0] - R2_target))
    seen_norm = set()
    out = []
    for R2_eq, Ra, Rb, Rc in candidates:
        norm = (min(Ra, Rb), max(Ra, Rb), Rc)
        if norm not in seen_norm:
            seen_norm.add(norm)
            err = abs(R2_eq - R2_target) / R2_target * 100
            out.append(dict(Ra=Ra, Rb=Rb, Rc=Rc, R2_eq=R2_eq, err=err))
        if len(out) >= top_n:
            break
    return out

# ════════════════════════════════════════════════════════════
# FUNCIÓN DE BÚSQUEDA
# ════════════════════════════════════════════════════════════

_ZETA_BANDS = [(100, 200), (200, 300), (300, 400), (400, 500),
               (500, 600), (600, 700), (700, 800), (800, 900), (900, 1001)]

def _cand_key(v):
    return (v['R1'], v['C1'], round(v['C2'], 25), round(v['R2_act'], 1))

def buscar(caps_search, label):
    print(f"  [{label}] Fase 1: búsqueda analítica (R1×C1×C2)...", flush=True)
    phase1 = []
    for R1 in _RES_EN_RANGO:
        for C1 in caps_search:
            tau1 = R1 * C1
            for C2 in caps_search:
                tau2_target = 1.0 / (omega0**2 * tau1)
                R2_i = tau2_target / C2
                if R2_i < R_min_val or R2_i > R2_max_tred:
                    continue
                R2_eff = R2_i * Rin / (R2_i + Rin)
                tau2   = R2_eff * C2
                zeta   = (tau1 + tau2) / (2*np.sqrt(tau1 * tau2))
                if not (ZETA_MIN <= zeta <= ZETA_MAX):
                    continue
                phase1.append(dict(R1=R1, C1=C1, C2=C2,
                                   R2_ideal=R2_i, zeta_approx=zeta))
    print(f"    {len(phase1)} candidatos en Fase 1.", flush=True)

    print(f"  [{label}] Fase 2: cuantizando y calculando error RMS (real vs deseada)...", flush=True)
    verified = []
    seen_keys = set()

    for cand in phase1:
        R1 = cand['R1']; C1 = cand['C1']; C2 = cand['C2']
        R2_i = cand['R2_ideal']

        R2_c       = res_comercial(R2_i)
        err_single = abs(R2_c - R2_i) / R2_i * 100
        tred_list  = mejores_tred(R2_i, top_n=5)
        tred_best  = tred_list[0] if tred_list else None
        err_tred   = tred_best['err'] if tred_best else float('inf')

        if tred_best and err_tred < err_single:
            R2_act  = tred_best['R2_eq']
            r2_type = 'T'
            r2_err  = err_tred
        else:
            R2_act  = R2_c
            r2_type = 'S'
            r2_err  = err_single

        key = (R1, C1, round(C2, 25), round(R2_act, 1))
        if key in seen_keys:
            continue
        seen_keys.add(key)

        f0v, zv = circuito_params(R1, R2_act, C1, C2)
        if f0v is None:
            continue
        f0_err = abs(f0v - f0_des) / f0_des * 100
        if f0_err > F0_TOL_PCT:
            continue
        if not (ZETA_MIN <= zv <= ZETA_MAX):
            continue

        err_rms = rms_error_dB(R1, R2_act, C1, C2)
        Rref    = res_comercial(R1 * R2_act / (R1 + R2_act))

        verified.append(dict(
            R1=R1, C1=C1, C2=C2,
            R2_ideal=R2_i, R2_act=R2_act,
            r2_type=r2_type, r2_err=r2_err,
            tred_list=tred_list,
            f0=f0v, f0_err=f0_err,
            zeta=zv, err_rms=err_rms,
            zeta_approx=cand['zeta_approx'],
            Rref=Rref
        ))

    verified.sort(key=lambda x: (x['err_rms'], x['f0_err']))

    results = []
    seen_cand = set()
    for lo, hi in _ZETA_BANDS:
        for v in verified:
            if lo <= v['zeta'] < hi and _cand_key(v) not in seen_cand:
                results.append(v)
                seen_cand.add(_cand_key(v))
                break
    for v in verified:
        if len(results) >= TOP_N:
            break
        if _cand_key(v) not in seen_cand:
            results.append(v)
            seen_cand.add(_cand_key(v))

    results.sort(key=lambda x: x['zeta'])
    print(f"    {len(verified)} candidatos verificados. Top {len(results)} por menor error RMS.", flush=True)
    return verified, results


verified_todos,     results_todos     = buscar(CAPS_TODOS,     "TODOS")
verified_ceramicos, results_ceramicos = buscar(CAPS_CERAMICOS, "CERÁMICOS")

GRUPOS = [
    ("TODOS LOS CAPACITORES",    "todos",     verified_todos,     results_todos),
    ("SOLO CERÁMICOS (≤ 100nF)", "ceramicos", verified_ceramicos, results_ceramicos),
]

# ════════════════════════════════════════════════════════════
# OUTPUT, CSV Y GRÁFICAS — un bloque por grupo
# ════════════════════════════════════════════════════════════
import matplotlib.pyplot as plt

SEP        = "=" * 128
sep        = "-" * 128
script_dir = os.path.dirname(os.path.abspath(__file__))
freqs_plot = np.logspace(-3, 6, 3000)
colores    = ['royalblue', 'forestgreen', 'firebrick', 'darkorange',
              'purple', 'teal', 'brown', 'crimson']

for grupo_label, grupo_slug, verified, results in GRUPOS:

    # ── Tabla principal ──────────────────────────────────────────────────────
    print()
    print(SEP)
    print(f"  GRUPO: {grupo_label}")
    print("  COMPENSADOR RC  —  Av(s) = -β(s) / [1 + ((1+s/ωp)/A0)·(1 + β(s) + (R2/Rin)/(sR2C2+1))]")
    print(f"  H_des = -2·(ζt-ζg)·ω0·s/(s²+2·ζt·ω0·s+ω0²)  |  ζ_geo={ZETA_GEO}  ζ_target={ZETA_TARGET}")
    print(f"  A0={A0_dB}dB  fp={f_p/1e6:.0f}MHz  Rin={fmt_res(Rin)}  |  Ranked por RMS error_dB (real vs deseada)")
    print(f"  R1 ∈ [{fmt_res(R_min_val)}, {fmt_res(R_max_val)}]  |  R2_max T-red: {fmt_res(R2_max_tred)}")
    print(SEP)
    print()
    print(f"{'#':<3} {'C1':>8} {'C2':>9} {'R1':>10} {'R2_ideal':>11} {'R2_act':>11}"
          f"  {'f0':>7} {'ζ':>8} {'f0err%':>7} {'errRMS_dB':>10}  R2  {'Rref':>11}")
    print(sep)

    for i, r in enumerate(results):
        print(f"{i+1:<3} {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>9}"
              f" {fmt_res(r['R1']):>10} {fmt_res(r['R2_ideal']):>11} {fmt_res(r['R2_act']):>11}"
              f"  {r['f0']:>7.3f} {r['zeta']:>8.2f} {r['f0_err']:>6.2f}%"
              f" {r['err_rms']:>9.4f}dB  {r['r2_type']}  {fmt_res(r['Rref']):>11}")

    # ── T-redes ──────────────────────────────────────────────────────────────
    print()
    print(sep)
    print("  T-REDES R2 (top 5 por candidato)  —  R2_eq = Ra + Rb + Ra·Rb/Rc")
    print("  Topología: Vout ─Ra─ M ─Rb─ V_minus  ,  Rc de M a GND")
    print(sep)
    for i, r in enumerate(results):
        c1_tag = '[E]' if r['C1'] > 100e-9 else '[C]'
        c2_tag = '[E]' if r['C2'] > 100e-9 else '[C]'
        print(f"\n  #{i+1}: C1={fmt_cap(r['C1'])}{c1_tag}  C2={fmt_cap(r['C2'])}{c2_tag}"
              f"  R1={fmt_res(r['R1'])}  R2_ideal={fmt_res(r['R2_ideal'])}"
              f"  ζ={r['zeta']:.1f}  errRMS={r['err_rms']:.4f}dB")
        for j, t in enumerate(r['tred_list'][:5]):
            used = " ◄ USADO" if r['r2_type'] == 'T' and j == 0 else ""
            print(f"    T{j+1}: Ra={fmt_res(t['Ra'])}  Rb={fmt_res(t['Rb'])}  Rc={fmt_res(t['Rc'])}"
                  f"  →  R2={fmt_res(t['R2_eq'])}  (err {t['err']:.3f}%){used}")
        if not r['tred_list']:
            R2_c = res_comercial(r['R2_ideal'])
            print(f"    S:  R2_comercial={fmt_res(R2_c)}  (err {r['r2_err']:.2f}%)")

    # ── CSV ──────────────────────────────────────────────────────────────────
    csv_path = os.path.join(script_dir, f"compensador_resultados_{grupo_slug}.csv")
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        header = ["rank", "C1_F", "C2_F", "R1_ohm", "R2_ideal_ohm", "R2_act_ohm", "Rref_ohm",
                  "f0_Hz", "f0_err_pct", "zeta", "err_rms_dB", "R2_type", "R2_err_pct"]
        for j in range(1, 6):
            header += [f"T{j}_Ra", f"T{j}_Rb", f"T{j}_Rc", f"T{j}_R2eq", f"T{j}_err_pct"]
        w.writerow(header)
        for i, r in enumerate(results):
            row = [i+1, r['C1'], r['C2'], r['R1'], r['R2_ideal'], r['R2_act'], r['Rref'],
                   r['f0'], round(r['f0_err'], 4), round(r['zeta'], 4),
                   round(r['err_rms'], 6), r['r2_type'], round(r['r2_err'], 4)]
            tlist = r['tred_list']
            for j in range(5):
                if j < len(tlist):
                    t = tlist[j]
                    row += [t['Ra'], t['Rb'], t['Rc'], round(t['R2_eq'], 2), round(t['err'], 4)]
                else:
                    row += ['', '', '', '', '']
            w.writerow(row)
    print(f"\n  CSV guardado: {csv_path}")

    # ── Gráfica ──────────────────────────────────────────────────────────────
    if not results:
        continue

    N_PLOT  = min(5, len(results))
    Hd_plot = H_deseada_arr(freqs_plot)
    mag_des = 20*np.log10(np.abs(Hd_plot))

    fig, (ax_mag, ax_err) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
    ax_mag.semilogx(freqs_plot, mag_des, color='black', ls='--', lw=2.0,
                    label=f'H_deseada (ζt={ZETA_TARGET}, ζg={ZETA_GEO})')

    for i in range(N_PLOT):
        r   = results[i]
        col = colores[i % len(colores)]
        R1, R2, C1, C2 = r['R1'], r['R2_act'], r['C1'], r['C2']

        Hr    = H_real_arr(R1, R2, C1, C2, freqs_plot)
        mag_r = 20*np.log10(np.abs(Hr))
        err_f = mag_r - mag_des

        lbl = (f"#{i+1} R1={fmt_res(R1)} C1={fmt_cap(C1)}"
               f" C2={fmt_cap(C2)} R2={fmt_res(R2)}"
               f" ζ={r['zeta']:.0f} errRMS={r['err_rms']:.1f}dB")

        ax_mag.semilogx(freqs_plot, mag_r, color=col, ls='-', lw=2.0, label=lbl)
        ax_err.semilogx(freqs_plot, err_f, color=col, lw=1.5,
                        label=f"#{i+1} ζ={r['zeta']:.0f}")

    ax_mag.axvline(f0_des, color='gray', ls=':', lw=1.0, label=f'f0={f0_des}Hz')
    ax_mag.set_ylabel('Magnitud (dB)')
    ax_mag.set_title(
        f'[{grupo_label}]  TF real (—) vs deseada (- -)  |  '
        f'ζ_geo={ZETA_GEO}  ζ_target={ZETA_TARGET}  f0={f0_des}Hz\n'
        f'H_des = -2·(ζt-ζg)·ω0·s / (s²+2·ζt·ω0·s+ω0²)'
    )
    ax_mag.legend(loc='lower right', fontsize=7)
    ax_mag.grid(True, which='both', ls=':', alpha=0.4)
    ax_mag.set_xlim([freqs_plot[0], freqs_plot[-1]])

    ax_err.axhline(0,      color='black', ls='-',  lw=0.8)
    ax_err.axvline(f0_des, color='gray',  ls=':',  lw=1.0)
    ax_err.set_xlabel('Frecuencia (Hz)')
    ax_err.set_ylabel('Error (dB) = 20·log|H_real / H_deseada|')
    ax_err.set_title(f'[{grupo_label}]  Error entre TF real y TF deseada')
    ax_err.legend(fontsize=8)
    ax_err.grid(True, which='both', ls=':', alpha=0.4)

    plt.tight_layout()
    plt.savefig(os.path.join(script_dir, f"compensador_plot_{grupo_slug}.png"), dpi=150)
    plt.show()
