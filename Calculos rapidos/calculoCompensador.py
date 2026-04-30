# -*- coding: utf-8 -*-
import sys, io
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import warnings
warnings.filterwarnings('ignore')

# ── Valores comerciales del kit ────────────────────────────────────────
RES_COMERCIALES = sorted([
    # Ohms
    1,1.2,1.5,1.6,2,2.2,2.4,2.7,3,3.3,3.6,3.9,4.3,4.7,5.1,5.6,6.2,6.8,7.5,8.2,9.1,
    10,12,15,18,20,22,24,27,30,33,36,39,43,47,51,56,62,68,75,82,91,
    100,120,150,180,200,220,240,270,300,330,360,390,430,470,510,560,620,680,750,820,910,
    # kΩ
    1e3,1.2e3,1.5e3,1.8e3,2e3,2.2e3,2.4e3,2.7e3,3e3,3.3e3,3.6e3,3.9e3,
    4.3e3,4.7e3,5.1e3,5.6e3,6.2e3,6.8e3,7.5e3,8.2e3,9.1e3,
    10e3,12e3,15e3,18e3,20e3,22e3,24e3,27e3,30e3,33e3,36e3,39e3,
    43e3,47e3,51e3,56e3,62e3,68e3,75e3,82e3,91e3,
    100e3,120e3,150e3,180e3,200e3,220e3,240e3,270e3,300e3,330e3,360e3,390e3,
    430e3,470e3,510e3,560e3,620e3,680e3,750e3,820e3,910e3,
    # MΩ
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

# Tabla de gains del PSoC PGA (Ra/Rb fijos en hardware)
PSOC_GAINS = [
    {'gain':  1, 'Ra': 40e3, 'Rb':    0},
    {'gain':  2, 'Ra': 40e3, 'Rb': 40e3},
    {'gain':  4, 'Ra': 40e3, 'Rb':120e3},
    {'gain':  8, 'Ra': 40e3, 'Rb':280e3},
    {'gain': 16, 'Ra': 40e3, 'Rb':600e3},
    {'gain': 24, 'Ra': 20e3, 'Rb':460e3},
    {'gain': 32, 'Ra': 20e3, 'Rb':620e3},
    {'gain': 48, 'Ra': 10e3, 'Rb':470e3},
    {'gain': 50, 'Ra': 10e3, 'Rb':490e3},
]

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

# ── Costo de calidad de componentes ────────────────────────────────────
def component_cost(R_list, C_list):
    """
    Penalidad continua: preferencia R≈100kΩ, C≤100nF.
    Sin hard-cutoffs: componentes malos tienen costo alto pero no se descartan.
    """
    cost = 0
    for R in R_list:
        log_r = np.log10(R / 100e3)   # 0 en R=100k
        if abs(log_r) <= 1:           # rango [10k, 1M]: zona cómoda
            cost += log_r**2
        else:                          # fuera de [10k, 1M]: penalidad 3×
            cost += 3 * log_r**2
    for C in C_list:
        if C > 100e-9:                 # electrolíticos: penalidad creciente
            cost += 10 * np.log10(C / 100e-9)**2
        # cerámicos ≤100nF: costo 0
    return cost

# ── Verificación de parámetros reales de los filtros ──────────────────
def verificar_sk(R1, R2, Rf, Ra, Rb, C1, C2):
    """
    H_SK(s) = K/(R1·C1) · s / (s² + BW·s + ω₀²)
    Retorna (f0_Hz, zeta, numerador_coef) o (None,None,None) si inválido.
    """
    if R1 <= 0 or R2 <= 0 or Rf <= 0 or Ra <= 0: return None, None, None
    K    = 1 + Rb/Ra
    w0sq = (1/R1 + 1/Rf) / (R2*C1*C2)
    if w0sq <= 0: return None, None, None
    w0   = np.sqrt(w0sq)
    bw   = 1/(R1*C1) + (1/C1 + 1/C2)/R2 - (K-1)/(Rf*C1)
    if bw <= 0: return None, None, None
    return w0/(2*np.pi), bw/(2*w0), K/(R1*C1)

def verificar_mfb(R1, R2, R5, C3, C4):
    """
    H_MFB(s) = -1/(R1·C4) · s / (s² + BW·s + ω₀²)  [signo negativo: inversora]
    Retorna (f0_Hz, zeta, numerador_coef) o (None,None,None) si inválido.
    """
    if R1 <= 0 or R2 <= 0 or R5 <= 0: return None, None, None
    bw   = (C3+C4)/(C3*C4*R5)
    w0sq = (1/R1 + 1/R2)/(R5*C3*C4)
    if w0sq <= 0 or bw <= 0: return None, None, None
    w0   = np.sqrt(w0sq)
    return w0/(2*np.pi), bw/(2*w0), 1/(R1*C4)

# ══════════════════════════════════════════════════════════════════════
# GRUPO 1 — Sallen-Key EXACTO
# Sweep: K(PSoC) × C1 × C2  →  R1, R2, Rf analíticos
#
# Ecuaciones (3 incógnitas, 3 ecs):
#   [Num]  K/(R1·C1) = target_num           → R1 = K/(target_num·C1)
#   [BW]   2γ₁ω₀ = K/(R1·C1)+(A/R2)−(K−1)·ω₀²·C2·R2
#          Sustituyendo [Num]: 2γ₀ω₀ = A/R2 − (K−1)·ω₀²·C2·R2
#          Cuadrática: (K−1)·ω₀²·C2·R2² + 2γ₀ω₀·R2 − A = 0
#   [ω₀²] 1/Rf = ω₀²·R2·C1·C2 − 1/R1
# ══════════════════════════════════════════════════════════════════════
def grupo1_sk_exacto(f0_des, gamma0, gamma1, omega0, target_num, lam):
    caps = [c for c in CAP_COMERCIALES if c >= 1e-9]
    resultados = {}   # clave→mejor resultado (deduplicación por componentes comerciales)

    for pg in PSOC_GAINS:
        K, Ra, Rb = pg['gain'], pg['Ra'], pg['Rb']

        for C1 in caps:
            R1_i = K / (target_num * C1)
            if R1_i <= 0: continue

            for C2 in caps:
                A = 1/C1 + 1/C2
                B = (K - 1) * omega0**2 * C2

                if K == 1:
                    denom = 2*gamma0*omega0
                    if denom == 0: continue
                    R2_i = A / denom
                else:
                    # (K−1)·ω₀²·C2·R2² + 2γ₀ω₀·R2 − A = 0
                    disc = (2*gamma0*omega0)**2 + 4*B*A
                    R2_i = (-2*gamma0*omega0 + np.sqrt(disc)) / (2*B)

                if R2_i <= 0: continue

                inv_Rf = omega0**2 * R2_i * C1*C2 - 1/R1_i
                if inv_Rf <= 0: continue
                Rf_i = 1/inv_Rf

                R1_c = res_comercial(R1_i); R2_c = res_comercial(R2_i)
                Rf_c = res_comercial(Rf_i); C1_c = cap_comercial(C1); C2_c = cap_comercial(C2)

                f0c, zetac, numc = verificar_sk(R1_c, R2_c, Rf_c, Ra, Rb, C1_c, C2_c)
                if f0c is None: continue

                err_f0   = abs(f0c   - f0_des)  / f0_des  * 100
                err_zeta = abs(zetac - gamma1)  / gamma1  * 100
                err_num  = abs(numc  - target_num) / target_num * 100
                cc = component_cost([R1_c, R2_c, Rf_c], [C1_c, C2_c])
                err_total = err_f0 + err_zeta + err_num + lam*cc

                entry = {
                    'gain': K, 'Ra': Ra, 'Rb': Rb,
                    'C1': C1_c, 'C2': C2_c,
                    'R1_ideal': R1_i, 'R2_ideal': R2_i, 'Rf_ideal': Rf_i,
                    'R1': R1_c, 'R2': R2_c, 'Rf': Rf_c,
                    'f0': f0c, 'zeta': zetac, 'num': numc,
                    'err_f0': err_f0, 'err_zeta': err_zeta, 'err_num': err_num,
                    'comp_cost': cc, 'err_total': err_total,
                }
                key = (K, C1_c, C2_c, R1_c, R2_c, Rf_c)
                if key not in resultados or err_total < resultados[key]['err_total']:
                    resultados[key] = entry

    return sorted(resultados.values(), key=lambda x: x['err_total'])

# ══════════════════════════════════════════════════════════════════════
# GRUPO 2 — MFB EXACTO  [salida invertida]
# Sweep: C3 × C4  →  R1, R5, R2 analíticos
#
# Ecuaciones (3 incógnitas, 3 ecs):
#   [Num]  1/(R1·C4) = target_num            → R1 = 1/(target_num·C4)
#   [BW]   (C3+C4)/(C3·C4·R5) = 2γ₁ω₀      → R5 = (C3+C4)/(2γ₁ω₀·C3·C4)
#   [ω₀²] 1/R2 = ω₀²·R5·C3·C4 − 1/R1
#               = ω₀(C3+C4)/(2γ₁) − target_num·C4
# ══════════════════════════════════════════════════════════════════════
def grupo2_mfb_exacto(f0_des, gamma1, omega0, target_num, lam):
    caps = [c for c in CAP_COMERCIALES if c >= 1e-9]
    resultados = {}

    for C3 in caps:
        for C4 in caps:
            R1_i   = 1 / (target_num * C4)
            R5_i   = (C3+C4) / (2*gamma1*omega0*C3*C4)
            inv_R2 = omega0*(C3+C4)/(2*gamma1) - target_num*C4
            if inv_R2 <= 0: continue
            R2_i = 1/inv_R2

            R1_c = res_comercial(R1_i); R2_c = res_comercial(R2_i)
            R5_c = res_comercial(R5_i); C3_c = cap_comercial(C3); C4_c = cap_comercial(C4)

            f0c, zetac, numc = verificar_mfb(R1_c, R2_c, R5_c, C3_c, C4_c)
            if f0c is None: continue

            err_f0   = abs(f0c   - f0_des)     / f0_des     * 100
            err_zeta = abs(zetac - gamma1)     / gamma1     * 100
            err_num  = abs(numc  - target_num) / target_num * 100
            cc = component_cost([R1_c, R2_c, R5_c], [C3_c, C4_c])
            err_total = err_f0 + err_zeta + err_num + lam*cc

            entry = {
                'C3': C3_c, 'C4': C4_c,
                'R1_ideal': R1_i, 'R2_ideal': R2_i, 'R5_ideal': R5_i,
                'R1': R1_c, 'R2': R2_c, 'R5': R5_c,
                'f0': f0c, 'zeta': zetac, 'num': numc,
                'err_f0': err_f0, 'err_zeta': err_zeta, 'err_num': err_num,
                'comp_cost': cc, 'err_total': err_total,
            }
            key = (C3_c, C4_c, R1_c, R2_c, R5_c)
            if key not in resultados or err_total < resultados[key]['err_total']:
                resultados[key] = entry

    return sorted(resultados.values(), key=lambda x: x['err_total'])

# ══════════════════════════════════════════════════════════════════════
# GRUPO 3 — Sallen-Key PARCIAL  (×k externo)
# Sweep: K × C1 × C2 × Rf  →  R1, R2 analíticos
# Solo matchea polos (ω₀, γ₁); el numerador difiere por factor k.
#
# Con D=ω₀²·C1·C2, P=2γ₁ω₀+K/(Rf·C1), A=1/C1+1/C2:
#   (D/C1)·R2² − P·R2 + A = 0   →  cuadrática en R2
#   1/R1 = D·R2 − 1/Rf
# ══════════════════════════════════════════════════════════════════════
def grupo3_sk_parcial(f0_des, gamma1, omega0, target_num, lam):
    caps = [c for c in CAP_COMERCIALES if c >= 1e-9]
    resultados = {}

    for pg in PSOC_GAINS:
        K, Ra, Rb = pg['gain'], pg['Ra'], pg['Rb']

        for C1 in caps:
            for C2 in caps:
                A = 1/C1 + 1/C2
                D = omega0**2 * C1*C2
                a_q = D/C1    # coeficiente cuadrático = ω₀²·C2

                for Rf in RES_COMERCIALES:
                    P    = 2*gamma1*omega0 + K/(Rf*C1)
                    disc = P**2 - 4*a_q*A
                    if disc < 0: continue

                    sq = np.sqrt(disc)
                    for R2_i in [(P + sq)/(2*a_q), (P - sq)/(2*a_q)]:
                        if R2_i <= 0: continue
                        inv_R1 = D*R2_i - 1/Rf
                        if inv_R1 <= 0: continue
                        R1_i = 1/inv_R1

                        R1_c = res_comercial(R1_i); R2_c = res_comercial(R2_i)
                        Rf_c = res_comercial(Rf);   C1_c = cap_comercial(C1); C2_c = cap_comercial(C2)

                        f0c, zetac, numc = verificar_sk(R1_c, R2_c, Rf_c, Ra, Rb, C1_c, C2_c)
                        if f0c is None: continue

                        err_f0   = abs(f0c   - f0_des) / f0_des  * 100
                        err_zeta = abs(zetac - gamma1)  / gamma1  * 100
                        cc = component_cost([R1_c, R2_c, Rf_c], [C1_c, C2_c])
                        err_total = err_f0 + err_zeta + lam*cc
                        k = numc / target_num

                        entry = {
                            'gain': K, 'Ra': Ra, 'Rb': Rb,
                            'C1': C1_c, 'C2': C2_c, 'Rf': Rf_c,
                            'R1_ideal': R1_i, 'R2_ideal': R2_i,
                            'R1': R1_c, 'R2': R2_c,
                            'f0': f0c, 'zeta': zetac, 'num': numc, 'k': k,
                            'err_f0': err_f0, 'err_zeta': err_zeta,
                            'comp_cost': cc, 'err_total': err_total,
                        }
                        key = (K, C1_c, C2_c, Rf_c, R1_c, R2_c)
                        if key not in resultados or err_total < resultados[key]['err_total']:
                            resultados[key] = entry

    return sorted(resultados.values(), key=lambda x: x['err_total'])

# ══════════════════════════════════════════════════════════════════════
# GRUPO 4 — MFB PARCIAL  [salida invertida]  (×k externo)
# Sweep: C3 × C4 × R1  →  R5, R2 analíticos
# Solo matchea polos; el numerador difiere por factor k.
#
#   R5 = (C3+C4)/(2γ₁ω₀·C3·C4)
#   1/R2 = ω₀²·R5·C3·C4 − 1/R1  =  ω₀(C3+C4)/(2γ₁) − 1/R1
# ══════════════════════════════════════════════════════════════════════
def grupo4_mfb_parcial(f0_des, gamma1, omega0, target_num, lam):
    caps = [c for c in CAP_COMERCIALES if c >= 1e-9]
    resultados = {}

    for C3 in caps:
        for C4 in caps:
            R5_i         = (C3+C4) / (2*gamma1*omega0*C3*C4)
            w0sq_R5C3C4  = omega0**2 * R5_i * C3*C4   # = ω₀(C3+C4)/(2γ₁)

            for R1 in RES_COMERCIALES:
                inv_R2 = w0sq_R5C3C4 - 1/R1
                if inv_R2 <= 0: continue
                R2_i = 1/inv_R2

                R1_c = res_comercial(R1);   R2_c = res_comercial(R2_i)
                R5_c = res_comercial(R5_i); C3_c = cap_comercial(C3); C4_c = cap_comercial(C4)

                f0c, zetac, numc = verificar_mfb(R1_c, R2_c, R5_c, C3_c, C4_c)
                if f0c is None: continue

                err_f0   = abs(f0c   - f0_des) / f0_des * 100
                err_zeta = abs(zetac - gamma1)  / gamma1 * 100
                cc = component_cost([R1_c, R2_c, R5_c], [C3_c, C4_c])
                err_total = err_f0 + err_zeta + lam*cc
                k = numc / target_num

                entry = {
                    'C3': C3_c, 'C4': C4_c, 'R1': R1_c,
                    'R2_ideal': R2_i, 'R5_ideal': R5_i,
                    'R2': R2_c, 'R5': R5_c,
                    'f0': f0c, 'zeta': zetac, 'num': numc, 'k': k,
                    'err_f0': err_f0, 'err_zeta': err_zeta,
                    'comp_cost': cc, 'err_total': err_total,
                }
                key = (C3_c, C4_c, R1_c, R2_c, R5_c)
                if key not in resultados or err_total < resultados[key]['err_total']:
                    resultados[key] = entry

    return sorted(resultados.values(), key=lambda x: x['err_total'])

# ════════════════════════════════════════════════════════════
# PARÁMETROS — editá acá
# ════════════════════════════════════════════════════════════
f0_des      = 10      # Hz — frecuencia natural del geófono
gamma0      = 0.25    # damping inicial del geófono (γ₀)
gamma1      = 10/0.1     # target damping (γ₁ > γ₀)
TOP_N       = 15      # resultados a mostrar por grupo
lambda_cost = 0.5     # peso de la calidad de componentes en err_total
# ════════════════════════════════════════════════════════════

omega0     = 2*np.pi*f0_des
target_num = 2*(gamma1 - gamma0)*omega0

print("="*82)
print(f"  COMPENSADOR DE GEOFONO -- PMC10059280")
print(f"  f0={f0_des} Hz  |  g0={gamma0}  |  g1={gamma1}  |  w0={omega0:.3f} rad/s")
print(f"  Objetivo: H_BP(s) = {target_num:.3f}*s / (s^2 + {2*gamma1*omega0:.3f}*s + {omega0**2:.3f})")
print("="*82)

# ── Grupo 1 ──────────────────────────────────────────────────────────
print("\n" + "-"*82)
print("  GRUPO 1 -- SALLEN-KEY EXACTO")
print("  Sweep K x C1 x C2 -> R1,R2,Rf analiticos | 3 ecuaciones, match completo de H_BP")
print("-"*82)
tops1 = grupo1_sk_exacto(f0_des, gamma0, gamma1, omega0, target_num, lambda_cost)
if not tops1:
    print("  Sin solución válida con los componentes del kit.")
else:
    n1 = min(TOP_N, len(tops1))
    print(f"\n  Top {n1} por menor error total:\n")
    print(f"{'#':<3} {'K':>3} {'C1':>8} {'C2':>8} {'R1':>10} {'R2':>10} {'Rf':>10}"
          f" {'f0(Hz)':>8} {'zeta':>6} {'errN%':>6} {'err%':>7}")
    print("-"*82)
    for i, r in enumerate(tops1[:n1]):
        print(f"{i+1:<3} {r['gain']:>3} {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>8}"
              f" {fmt_res(r['R1']):>10} {fmt_res(r['R2']):>10} {fmt_res(r['Rf']):>10}"
              f" {r['f0']:>8.3f} {r['zeta']:>6.3f} {r['err_num']:>6.2f} {r['err_total']:>7.2f}")
    b = tops1[0]
    print(f"\n  MEJOR G1: K={b['gain']}  C1={fmt_cap(b['C1'])}  C2={fmt_cap(b['C2'])}"
          f"  R1={fmt_res(b['R1'])}  R2={fmt_res(b['R2'])}  Rf={fmt_res(b['Rf'])}")
    print(f"    f0={b['f0']:.4f}Hz (err {b['err_f0']:.2f}%)  |"
          f"  zeta={b['zeta']:.4f} (err {b['err_zeta']:.2f}%)  |"
          f"  num={b['num']:.4f} (err {b['err_num']:.2f}%)")

# ── Grupo 2 ──────────────────────────────────────────────────────────
print("\n" + "-"*82)
print("  GRUPO 2 -- MFB EXACTO  [! salida invertida -> conectar sumador como +, no -]")
print("  Sweep C3 x C4 -> R1,R5,R2 analiticos | 3 ecuaciones, match completo de H_BP")
print("-"*82)
tops2 = grupo2_mfb_exacto(f0_des, gamma1, omega0, target_num, lambda_cost)
if not tops2:
    print("  Sin solucion valida (g1 grande requiere C3 >> C4 -- puede ser infactible en kit).")
else:
    n2 = min(TOP_N, len(tops2))
    print(f"\n  Top {n2} por menor error total:\n")
    print(f"{'#':<3} {'C3':>8} {'C4':>8} {'R1':>10} {'R2':>10} {'R5':>10}"
          f" {'f0(Hz)':>8} {'zeta':>6} {'errN%':>6} {'err%':>7}")
    print("-"*82)
    for i, r in enumerate(tops2[:n2]):
        print(f"{i+1:<3} {fmt_cap(r['C3']):>8} {fmt_cap(r['C4']):>8}"
              f" {fmt_res(r['R1']):>10} {fmt_res(r['R2']):>10} {fmt_res(r['R5']):>10}"
              f" {r['f0']:>8.3f} {r['zeta']:>6.3f} {r['err_num']:>6.2f} {r['err_total']:>7.2f}")
    b = tops2[0]
    print(f"\n  MEJOR G2: C3={fmt_cap(b['C3'])}  C4={fmt_cap(b['C4'])}"
          f"  R1={fmt_res(b['R1'])}  R2={fmt_res(b['R2'])}  R5={fmt_res(b['R5'])}")
    print(f"    f0={b['f0']:.4f}Hz (err {b['err_f0']:.2f}%)  |"
          f"  zeta={b['zeta']:.4f} (err {b['err_zeta']:.2f}%)  |"
          f"  num={b['num']:.4f} (err {b['err_num']:.2f}%)")

# ── Grupo 3 ──────────────────────────────────────────────────────────
print("\n" + "-"*82)
print("  GRUPO 3 -- SALLEN-KEY PARCIAL  (multiplicar salida por k para completar H_BP)")
print("  Sweep K x C1 x C2 x Rf -> R1,R2 analiticos | 2 ecuaciones, polos exactos")
print("-"*82)
print("  Calculando (puede tardar unos segundos)...")
tops3 = grupo3_sk_parcial(f0_des, gamma1, omega0, target_num, lambda_cost)
if not tops3:
    print("  Sin solucion valida.")
else:
    n3 = min(TOP_N, len(tops3))
    print(f"\n  Top {n3} por menor error total:\n")
    print(f"{'#':<3} {'K':>3} {'C1':>8} {'C2':>8} {'Rf':>10} {'R1':>10} {'R2':>10}"
          f" {'f0(Hz)':>8} {'zeta':>6} {'k':>7} {'err%':>7}")
    print("-"*82)
    for i, r in enumerate(tops3[:n3]):
        print(f"{i+1:<3} {r['gain']:>3} {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>8}"
              f" {fmt_res(r['Rf']):>10} {fmt_res(r['R1']):>10} {fmt_res(r['R2']):>10}"
              f" {r['f0']:>8.3f} {r['zeta']:>6.3f} {r['k']:>7.4f} {r['err_total']:>7.2f}")
    b = tops3[0]
    print(f"\n  MEJOR G3: K={b['gain']}  C1={fmt_cap(b['C1'])}  C2={fmt_cap(b['C2'])}"
          f"  Rf={fmt_res(b['Rf'])}  R1={fmt_res(b['R1'])}  R2={fmt_res(b['R2'])}")
    print(f"    f0={b['f0']:.4f}Hz (err {b['err_f0']:.2f}%)  |"
          f"  zeta={b['zeta']:.4f} (err {b['err_zeta']:.2f}%)  |  k={b['k']:.4f}")

# ── Grupo 4 ──────────────────────────────────────────────────────────
print("\n" + "-"*82)
print("  GRUPO 4 -- MFB PARCIAL  [! invertido]  (x k externo para completar H_BP)")
print("  Sweep C3 x C4 x R1 -> R5,R2 analiticos | 2 ecuaciones, polos exactos")
print("-"*82)
tops4 = grupo4_mfb_parcial(f0_des, gamma1, omega0, target_num, lambda_cost)
if not tops4:
    print("  Sin solucion valida.")
else:
    n4 = min(TOP_N, len(tops4))
    print(f"\n  Top {n4} por menor error total:\n")
    print(f"{'#':<3} {'C3':>8} {'C4':>8} {'R1':>10} {'R2':>10} {'R5':>10}"
          f" {'f0(Hz)':>8} {'zeta':>6} {'k':>7} {'err%':>7}")
    print("-"*82)
    for i, r in enumerate(tops4[:n4]):
        print(f"{i+1:<3} {fmt_cap(r['C3']):>8} {fmt_cap(r['C4']):>8}"
              f" {fmt_res(r['R1']):>10} {fmt_res(r['R2']):>10} {fmt_res(r['R5']):>10}"
              f" {r['f0']:>8.3f} {r['zeta']:>6.3f} {r['k']:>7.4f} {r['err_total']:>7.2f}")
    b = tops4[0]
    print(f"\n  MEJOR G4: C3={fmt_cap(b['C3'])}  C4={fmt_cap(b['C4'])}"
          f"  R1={fmt_res(b['R1'])}  R2={fmt_res(b['R2'])}  R5={fmt_res(b['R5'])}")
    print(f"    f0={b['f0']:.4f}Hz (err {b['err_f0']:.2f}%)  |"
          f"  zeta={b['zeta']:.4f} (err {b['err_zeta']:.2f}%)  |  k={b['k']:.4f}")

# ════════════════════════════════════════════════════════════
# GRAFICACIÓN — editá los índices (1-based) y re-ejecutá
#
# grupo1 / grupo3: Sallen-Key
# grupo2 / grupo4: MFB  (magnitud idéntica, salida eléctrica invertida)
#
# Para grupos parciales (3 y 4) se grafica la respuesta real del filtro
# Y también la versión escalada ×k que iguala el numerador objetivo
# (línea punteada del mismo color).
# ════════════════════════════════════════════════════════════
GRAFICAR = {
    'grupo1': [1],   # índices de candidatos del Grupo 1 a superponer
    'grupo2': [1],    # índices del Grupo 2
    'grupo3': [1],    # índices del Grupo 3
    'grupo4': [1],    # índices del Grupo 4
}
# ════════════════════════════════════════════════════════════

import matplotlib.pyplot as plt
from scipy.signal import TransferFunction, bode as scipy_bode

def _bode_mag(num, den, freqs_hz):
    """Magnitud en dB evaluada en freqs_hz [Hz]."""
    w = 2*np.pi*freqs_hz
    _, mag, _ = scipy_bode(TransferFunction(num, den), w=w)
    return mag

def _label_sk(r, idx, grupo):
    K  = r['gain']
    Rf = fmt_res(r.get('Rf', 0))
    return (f"G{grupo}-SK #{idx}: K={K}  "
            f"C1={fmt_cap(r['C1'])} C2={fmt_cap(r['C2'])}  "
            f"R1={fmt_res(r['R1'])} R2={fmt_res(r['R2'])} Rf={Rf}")

def _label_mfb(r, idx, grupo):
    return (f"G{grupo}-MFB #{idx}:  "
            f"C3={fmt_cap(r['C3'])} C4={fmt_cap(r['C4'])}  "
            f"R1={fmt_res(r['R1'])} R2={fmt_res(r['R2'])} R5={fmt_res(r['R5'])}")

# Rango de frecuencias (3 décadas alrededor de f0)
freqs = np.logspace(np.log10(f0_des / 10000), np.log10(f0_des * 10000), 4000)

any_sel = any(len(v) > 0 for v in GRAFICAR.values())
if not any_sel:
    print("\n  [Grafico desactivado -- agrega indices a GRAFICAR y re-ejecuta]")
else:
    # ── Ideal ────────────────────────────────────────────────────────────
    mag_ideal = _bode_mag([target_num, 0], [1, 2*gamma1*omega0, omega0**2], freqs)

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.semilogx(freqs, mag_ideal, 'k-', linewidth=2.5, zorder=10,
                label=f'Ideal H_BP  (g0={gamma0}, g1={gamma1}, f0={f0_des}Hz)')

    # Paleta por grupo
    paletas = {
        1: ['royalblue',   'cornflowerblue', 'steelblue',   'deepskyblue'],
        2: ['forestgreen', 'mediumseagreen', 'limegreen',   'darkgreen'],
        3: ['firebrick',   'tomato',         'salmon',       'coral'],
        4: ['darkorange',  'goldenrod',      'chocolate',    'peru'],
    }

    grupos_cfg = [
        (1, tops1, False),   # (numero_grupo, lista_tops, es_mfb)
        (2, tops2, True),
        (3, tops3, False),
        (4, tops4, True),
    ]

    for gnum, tops_list, es_mfb in grupos_cfg:
        gkey = f'grupo{gnum}'
        indices = GRAFICAR.get(gkey, [])
        if not indices or not tops_list:
            continue

        colores = paletas[gnum]

        for ii, idx in enumerate(indices):
            idx0 = idx - 1
            if idx0 < 0 or idx0 >= len(tops_list):
                print(f"  [!] Indice {idx} fuera de rango para grupo{gnum} "
                      f"(max={len(tops_list)})")
                continue

            r    = tops_list[idx0]
            col  = colores[ii % len(colores)]
            w0_c = 2*np.pi * r['f0']
            bw_c = 2*r['zeta'] * w0_c
            N_c  = r['num']               # numerador real del filtro
            den_c = [1, bw_c, w0_c**2]

            lbl = _label_mfb(r, idx, gnum) if es_mfb else _label_sk(r, idx, gnum)

            # Respuesta real del filtro (magnitud = misma con/sin signo MFB)
            mag_r = _bode_mag([N_c, 0], den_c, freqs)
            ax.semilogx(freqs, mag_r, color=col, linewidth=1.8, label=lbl)

            # Grupos parciales (3,4): también la versión escalada ×k
            if 'k' in r and abs(r['k'] - 1.0) > 0.01:
                N_k   = N_c * r['k']
                mag_k = _bode_mag([N_k, 0], den_c, freqs)
                ax.semilogx(freqs, mag_k, color=col, linewidth=1.2, linestyle='--',
                            label=f"  -> x k={r['k']:.4f}")

    ax.axvline(f0_des, color='gray', ls=':', lw=1.2, label=f'f0 = {f0_des} Hz')
    ax.set_xlabel('Frecuencia (Hz)')
    ax.set_ylabel('Magnitud (dB)')
    ax.set_title(f'Comparacion candidatos vs H_BP ideal\n'
                 f'f0={f0_des}Hz  |  g0={gamma0}  |  g1={gamma1}  |  '
                 f'target_num={target_num:.3f}')
    ax.grid(True, which='both', ls=':', alpha=0.5)
    ax.legend(loc='lower right', fontsize=7.5)
    plt.tight_layout()
    plt.show()
