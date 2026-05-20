# -*- coding: utf-8 -*-
"""
calculoMFB_LPF.py
Diseño de filtro MFB Lowpass con valores comerciales.

Topología (ver mfb_lpf_circuit.png):
  Vi --R3--+--R2--(-) op-amp (+)--Vo
           |              |   |
           C1             C2  R1
           |              +---+
          GND

Función de transferencia (nota: MFB invierte la señal):
  H(s) = 1/(R2·R3·C1·C2) / [s² + (1/C1)(1/R1+1/R2+1/R3)·s + 1/(R1·R2·C1·C2)]
       = K·ω0² / (s² + 2ζω0·s + ω0²)

Ecuaciones de diseño:
  a0 = ω0²    = 1/(R1·R2·C1·C2)
  a1 = 2ζω0   = (1/C1)·(1/R1 + 1/R2 + 1/R3)
  b0 = K·ω0²  = 1/(R2·R3·C1·C2)

  De b0/a0 = K:  R3 = R1/K

  Cuadrática en R1 (dado C1, C2):
    ω0²·C1·C2·R1² − 2ζω0·C1·R1 + (1+K) = 0
    R1 = [ζ ± √(ζ²−(1+K)·C2/C1)] / (ω0·C2)
    Condición:  C2 ≤ C1·ζ²/(1+K)

  R2 = 1 / (ω0²·C1·C2·R1)
  R3 = R1 / K
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import warnings
warnings.filterwarnings('ignore')

# ── Valores comerciales ────────────────────────────────────────────────
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
    if c >= 1e-6: return f"{c*1e6:.3g}uF"
    if c >= 1e-9: return f"{c*1e9:.3g}nF"
    return f"{c*1e12:.3g}pF"

def fmt_res(r):
    if r >= 1e6: return f"{r/1e6:.3g}Mohm"
    if r >= 1e3: return f"{r/1e3:.3g}kohm"
    return f"{r:.3g}ohm"

# ════════════════════════════════════════════════════════════
# PARÁMETROS — editá acá
# ════════════════════════════════════════════════════════════
f0_des      = 300    # Hz — frecuencia de corte (-3 dB)
zeta_des    = 0.691   # factor de amortiguamiento (0.707 = Butterworth)
K_des       = 5.0     # ganancia DC (magnitud; el MFB es inversor)
TOP_N       = 20      # resultados a mostrar
lambda_cost = 1       # peso del costo de componentes

# Restricciones de impedancia del op-amp
R_OUT_OPAMP        = 20     # Ω  — impedancia de salida
R_IN_OPAMP         = 35e6   # Ω  — impedancia de entrada (~35 MΩ)
IMPEDANCIA_ESCALAR = 40     # R ∈ [R_out×esc, R_in/esc] = [800Ω, 875kΩ]
# ════════════════════════════════════════════════════════════

omega0    = 2*np.pi*f0_des
R_min_val = 1e3#R_OUT_OPAMP * IMPEDANCIA_ESCALAR
R_max_val = 1e6#R_IN_OPAMP  / IMPEDANCIA_ESCALAR

def in_range(R):
    return R_min_val <= R <= R_max_val

def cap_cost(C_list):
    cost = 0
    for C in C_list:
        if C > 100e-9:
            cost += 20 * np.log10(C / 100e-9)**2
    return cost

# ══════════════════════════════════════════════════════════════════════
# VERIFICACIÓN
# ══════════════════════════════════════════════════════════════════════

def verificar_mfb(R1, R2, R3, C1, C2):
    """
    Calcula f0, ζ, K reales a partir de los valores de componentes.
    Retorna (f0_Hz, zeta, K) o (None, None, None) si inválido.
    """
    if any(v <= 0 for v in [R1, R2, R3, C1, C2]): return None, None, None
    a0 = 1.0 / (R1*R2*C1*C2)
    a1 = (1.0/C1) * (1.0/R1 + 1.0/R2 + 1.0/R3)
    b0 = 1.0 / (R2*R3*C1*C2)
    if a0 <= 0 or a1 <= 0: return None, None, None
    w0   = np.sqrt(a0)
    zeta = a1 / (2*w0)
    gain = b0 / a0  # = R1/R3
    return w0/(2*np.pi), zeta, gain

# ══════════════════════════════════════════════════════════════════════
# BÚSQUEDA
#
# Para cada par (C1, C2) comercial con C2 ≤ C1·ζ²/(1+K):
#   Resuelve cuadrática en R1:
#     R1 = [ζ ± √(ζ²−(1+K)·C2/C1)] / (ω0·C2)
#   Luego:
#     R2 = 1/(ω0²·C1·C2·R1)
#     R3 = R1/K
# ══════════════════════════════════════════════════════════════════════

def buscar_mfb(f0_des, zeta_des, K_des, lam):
    C2_max_ratio = zeta_des**2 / (1 + K_des)   # condición C2/C1 ≤ este valor
    resultados = {}

    for C1 in CAP_COMERCIALES:
        C2_max = C1 * C2_max_ratio
        for C2 in CAP_COMERCIALES:
            if C2 > C2_max: continue
            D = zeta_des**2 - (1 + K_des) * C2/C1
            if D < 0: continue
            sqrtD = np.sqrt(D)

            for sign in [+1, -1]:
                R1_i = (zeta_des + sign*sqrtD) / (omega0 * C2)
                if R1_i <= 0: continue
                R2_i = 1.0 / (omega0**2 * C1 * C2 * R1_i)
                R3_i = R1_i / K_des
                if R2_i <= 0 or R3_i <= 0: continue

                C1_c = cap_comercial(C1)
                C2_c = cap_comercial(C2)
                R1_c = res_comercial(R1_i)
                R2_c = res_comercial(R2_i)
                R3_c = res_comercial(R3_i)

                # Filtro duro: todos los R en rango
                if not (in_range(R1_c) and in_range(R2_c) and in_range(R3_c)):
                    continue

                f0c, zetac, Kc = verificar_mfb(R1_c, R2_c, R3_c, C1_c, C2_c)
                if f0c is None: continue

                ef = abs(f0c   - f0_des)   / f0_des   * 100
                ez = abs(zetac - zeta_des) / zeta_des * 100
                ek = abs(Kc    - K_des)    / max(K_des, 1e-9) * 100
                cc = cap_cost([C1_c, C2_c])
                et = ef + ez + ek + lam*cc

                entry = dict(
                    C1=C1_c, C2=C2_c,
                    R1_ideal=R1_i, R2_ideal=R2_i, R3_ideal=R3_i,
                    R1=R1_c, R2=R2_c, R3=R3_c,
                    f0=f0c, zeta=zetac, K=Kc,
                    ef=ef, ez=ez, ek=ek, cc=cc, et=et,
                )
                key = (C1_c, C2_c, R1_c, R2_c, R3_c)
                if key not in resultados or et < resultados[key]['et']:
                    resultados[key] = entry

    return sorted(resultados.values(), key=lambda x: x['et'])

# ── Ejecutar búsqueda ─────────────────────────────────────────────────
tops = buscar_mfb(f0_des, zeta_des, K_des, lambda_cost)

SEP = "-"*84
print("="*84)
print("  MFB LOWPASS FILTER — calculoMFB_LPF.py")
print(f"  Target: f0={f0_des/1e3:.3g}kHz  ζ={zeta_des}  K={K_des}")
print(f"  ω0={omega0:.2f}rad/s  |  R en [{fmt_res(R_min_val)}, {fmt_res(R_max_val)}]  (escalar={IMPEDANCIA_ESCALAR})")
print(f"  C2_max/C1 = ζ²/(1+K) = {zeta_des**2/(1+K_des):.4f}")
print("="*84)

if not tops:
    print("  Sin solución válida. Probá ajustar IMPEDANCIA_ESCALAR o los parámetros.")
else:
    n = min(TOP_N, len(tops))
    print(f"\n  Top {n} por menor error total (R1,R2,R3 en rango, C cerámica preferida):\n")
    print(f"{'#':<3} {'C1':>8} {'C2':>8} {'R1':>10} {'R2':>10} {'R3':>10}"
          f"  {'f0(Hz)':>9} {'ζ':>6} {'K':>6}  {'ef%':>5} {'ez%':>5} {'ek%':>5} {'err%':>7}")
    print(SEP)
    for i, r in enumerate(tops[:n]):
        print(f"{i+1:<3} {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>8}"
              f" {fmt_res(r['R1']):>10} {fmt_res(r['R2']):>10} {fmt_res(r['R3']):>10}"
              f"  {r['f0']:>9.2f} {r['zeta']:>6.4f} {r['K']:>6.3f}"
              f"  {r['ef']:>5.2f} {r['ez']:>5.2f} {r['ek']:>5.2f} {r['et']:>7.2f}")

    b = tops[0]
    print(f"\n{'='*84}")
    print("  MEJOR SOLUCIÓN:")
    print(f"    C1 = {fmt_cap(b['C1'])}   C2 = {fmt_cap(b['C2'])}")
    print(f"    R1 = {fmt_res(b['R1'])}   R2 = {fmt_res(b['R2'])}   R3 = {fmt_res(b['R3'])}")
    print(f"    Ideales: R1={fmt_res(b['R1_ideal'])} R2={fmt_res(b['R2_ideal'])} R3={fmt_res(b['R3_ideal'])}")
    print(f"    f0 = {b['f0']:.4f} Hz  (err {b['ef']:.2f}%)")
    print(f"    ζ  = {b['zeta']:.5f}   (err {b['ez']:.2f}%)")
    print(f"    K  = {b['K']:.5f}    (err {b['ek']:.2f}%)")
    print(f"{'='*84}")

# ════════════════════════════════════════════════════════════
# GRAFICACIÓN
# ════════════════════════════════════════════════════════════
GRAFICAR = [1, 2, 3]   # índices 1-based de los candidatos a superponer

import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.gridspec as gridspec
from scipy.signal import TransferFunction, bode as scipy_bode

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CIRCUIT_IMG = os.path.join(_SCRIPT_DIR, 'mfb_lpf_circuit.png')

def bode_mfb(R1, R2, R3, C1, C2, freqs_hz):
    """Bode con polinomio directo."""
    b0 = 1.0 / (R2*R3*C1*C2)
    a1 = (1.0/C1)*(1.0/R1 + 1.0/R2 + 1.0/R3)
    a0 = 1.0 / (R1*R2*C1*C2)
    _, mag, phase = scipy_bode(TransferFunction([b0], [1, a1, a0]),
                               w=2*np.pi*freqs_hz)
    return mag, phase

if tops and GRAFICAR:
    freqs = np.logspace(np.log10(f0_des/100), np.log10(f0_des*100), 3000)

    # ── Layout: Bode arriba, circuito abajo ──────────────────────────
    has_img = os.path.isfile(_CIRCUIT_IMG)
    if has_img:
        fig = plt.figure(figsize=(14, 9))
        gs  = gridspec.GridSpec(2, 2, height_ratios=[2.2, 1],
                                hspace=0.35, wspace=0.3)
        ax_mag   = fig.add_subplot(gs[0, 0])
        ax_phase = fig.add_subplot(gs[0, 1])
        ax_circ  = fig.add_subplot(gs[1, :])
    else:
        fig, (ax_mag, ax_phase) = plt.subplots(1, 2, figsize=(14, 5))

    # Ideal
    b0_id = K_des * omega0**2
    a1_id = 2*zeta_des*omega0
    _, mag_id, phase_id = scipy_bode(TransferFunction([b0_id], [1, a1_id, omega0**2]),
                                     w=2*np.pi*freqs)
    ax_mag.semilogx(freqs, mag_id, 'k-', lw=2.5, zorder=10,
                    label=f'Ideal  f0={f0_des/1e3:.3g}kHz ζ={zeta_des} K={K_des}')
    ax_phase.semilogx(freqs, phase_id, 'k-', lw=2.5, zorder=10, label='Ideal')

    colores = ['royalblue','forestgreen','firebrick','darkorange','purple',
               'cornflowerblue','mediumseagreen','tomato']

    for ii, idx in enumerate(GRAFICAR):
        idx0 = idx - 1
        if idx0 < 0 or idx0 >= len(tops): continue
        r   = tops[idx0]
        col = colores[ii % len(colores)]
        mag, phase = bode_mfb(r['R1'], r['R2'], r['R3'], r['C1'], r['C2'], freqs)
        lbl = (f"#{idx}: R1={fmt_res(r['R1'])} R2={fmt_res(r['R2'])} R3={fmt_res(r['R3'])}"
               f" C1={fmt_cap(r['C1'])} C2={fmt_cap(r['C2'])}"
               f"  ζ={r['zeta']:.3f} K={r['K']:.3f}")
        ax_mag.semilogx(freqs, mag, color=col, lw=1.8, label=lbl)
        ax_phase.semilogx(freqs, phase, color=col, lw=1.8, label=f'#{idx}')

    for ax in [ax_mag, ax_phase]:
        ax.axvline(f0_des, color='gray', ls=':', lw=1.2,
                   label=f'f0={f0_des/1e3:.3g}kHz')
        ax.grid(True, which='both', ls=':', alpha=0.5)
        ax.set_xlabel('Frecuencia (Hz)')

    ax_mag.set_ylabel('Magnitud (dB)')
    ax_mag.set_title(f'MFB LPF — Magnitud\nf0={f0_des/1e3:.3g}kHz  ζ={zeta_des}  K={K_des}')
    ax_mag.legend(loc='lower left', fontsize=6.5)

    ax_phase.set_ylabel('Fase (°)')
    ax_phase.set_title('MFB LPF — Fase')
    ax_phase.legend(loc='lower left', fontsize=7)

    if has_img:
        img = mpimg.imread(_CIRCUIT_IMG)
        ax_circ.imshow(img)
        ax_circ.axis('off')
        ax_circ.set_title('Circuito MFB Lowpass', fontsize=9)

    plt.tight_layout()
    plt.show()
