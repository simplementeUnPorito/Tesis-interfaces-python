import numpy as np
from scipy.optimize import fsolve
import warnings
warnings.filterwarnings('ignore')

# ── Valores comerciales de tu kit ───────────────────────────
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

def cap_comercial(v):
    return CAP_COMERCIALES[np.argmin([abs(c-v) for c in CAP_COMERCIALES])]

def res_comercial(v):
    return RES_COMERCIALES[np.argmin([abs(r-v) for r in RES_COMERCIALES])]

def fmt_cap(c):
    if c >= 1e-6: return f"{c*1e6:.2f} µF"
    if c >= 1e-9: return f"{c*1e9:.2f} nF"
    return f"{c*1e12:.2f} pF"

def fmt_res(r):
    if r >= 1e6: return f"{r/1e6:.2f} MΩ"
    if r >= 1e3: return f"{r/1e3:.2f} kΩ"
    return f"{r:.1f} Ω"

def verificar(R1, R2, Rf, Ra, Rb, C1, C2):
    w0sq = (Rf + R1) / (R1 * Rf * R2 * C1 * C2)
    if w0sq <= 0: return None, None
    w0  = np.sqrt(w0sq)
    bw  = 1/(R1*C1) + 1/(R2*C1) + 1/(R2*C2) - Rb/(Ra*Rf*C1)
    if bw <= 0: return None, None
    Q   = w0 / bw
    return w0/(2*np.pi), 1/(2*Q)

def resolver(f0_target, zeta_target, Ra, Rb, Rf, C1, C2):
    w0 = 2*np.pi*f0_target
    Q  = 1.0/(2.0*zeta_target)

    def ecuaciones(p):
        R1, R2 = p
        if R1 <= 0 or R2 <= 0: return [1e10, 1e10]
        f1 = (Rf+R1)/(R1*Rf*R2*C1*C2) - w0**2
        f2 = 1/(R1*C1)+1/(R2*C1)+1/(R2*C2)-Rb/(Ra*Rf*C1) - w0/Q
        return [f1, f2]

    mejor = None
    for r1i in np.logspace(0, 7, 15):
        for r2i in np.logspace(0, 7, 15):
            try:
                sol, info, ier, _ = fsolve(ecuaciones, [r1i, r2i], full_output=True)
                R1s, R2s = sol
                if ier==1 and R1s>0 and R2s>0:
                    res = np.linalg.norm(info['fvec'])
                    if mejor is None or res < mejor[1]:
                        mejor = (sol, res)
            except: continue

    if mejor is None or mejor[1] > 1e-2: return None
    return mejor[0]

def barrido_capacitores(f0_des, zeta_des, Ra, Rb, Rf, R2_max=2e6):
    """
    Prueba todos los pares C1/C2 de tu kit y devuelve
    las combinaciones con R1 y R2 dentro de rango, ordenadas por error.
    """
    resultados = []
    caps_utiles = [c for c in CAP_COMERCIALES if c >= 1e-9]  # >= 1nF

    for C1 in caps_utiles:
        for C2 in caps_utiles:
            sol = resolver(f0_des, zeta_des, Ra, Rb, Rf, C1, C2)
            if sol is None: continue
            R1_i, R2_i = sol
            # Filtro: dentro de rango de tu kit
            if R1_i < 1 or R1_i > 10e6: continue
            if R2_i < 1 or R2_i > 10e6: continue

            R1_c = res_comercial(R1_i)
            R2_c = res_comercial(R2_i)
            C1_c = cap_comercial(C1)
            C2_c = cap_comercial(C2)

            f0_c, zeta_c = verificar(R1_c, R2_c, Rf, Ra, Rb, C1_c, C2_c)
            if f0_c is None: continue

            err_f0   = abs(f0_c   - f0_des)   / f0_des   * 100
            err_zeta = abs(zeta_c - zeta_des) / zeta_des * 100
            err_total = err_f0 + err_zeta

            resultados.append({
                'C1': C1_c, 'C2': C2_c,
                'R1_ideal': R1_i, 'R2_ideal': R2_i,
                'R1_com': R1_c,   'R2_com': R2_c,
                'f0_com': f0_c,   'zeta_com': zeta_c,
                'err_f0': err_f0, 'err_zeta': err_zeta,
                'err_total': err_total
            })

    return sorted(resultados, key=lambda x: x['err_total'])

# ════════════════════════════════════════════════════════════
# PARÁMETROS — editá acá
# ════════════════════════════════════════════════════════════
f0_des   = 10
wn  = 2*np.pi*f0_des
fLow    = 1
wLow    = 2*np.pi*fLow

zeta_des = wn/(2*wLow)  
Ra = 40e3
Rb = 0e3
Rf = 47e3
TOP_N = 15   # cuántas combinaciones mostrar
# ════════════════════════════════════════════════════════════

print("="*72)
print(f"  BARRIDO C1/C2 — f0={f0_des} Hz | zeta={zeta_des} | Q={1/(2*zeta_des):.4f}")
print(f"  Ra={fmt_res(Ra)} | Rb={fmt_res(Rb)} | Rf={fmt_res(Rf)} | K={1+Rb/Ra:.1f}")
print("="*72)
print(f"  Buscando mejores combinaciones (puede tardar ~10s)...")

tops = barrido_capacitores(f0_des, zeta_des, Ra, Rb, Rf)

if not tops:
    print("\n  Sin solución válida con ningún par C1/C2 del kit.")
    print("  Revisá Ra, Rb, Rf — puede que la ganancia K no sea compatible.")
else:
    print(f"\n  Top {min(TOP_N, len(tops))} combinaciones por menor error total:\n")
    print(f"{'#':<3} {'C1':>9} {'C2':>9} {'R1 ideal':>12} {'R1 com':>10} {'R2 ideal':>12} {'R2 com':>10} {'f0':>8} {'zeta':>7} {'err%':>7}")
    print("-"*100)
    for i, r in enumerate(tops[:TOP_N]):
        print(f"{i+1:<3} {fmt_cap(r['C1']):>9} {fmt_cap(r['C2']):>9} "
              f"{fmt_res(r['R1_ideal']):>12} {fmt_res(r['R1_com']):>10} "
              f"{fmt_res(r['R2_ideal']):>12} {fmt_res(r['R2_com']):>10} "
              f"{r['f0_com']:>8.3f} {r['zeta_com']:>7.2f} {r['err_total']:>7.2f}")

    best = tops[0]
    print(f"\n{'─'*72}")
    print(f"  MEJOR SOLUCIÓN:")
    print(f"    C1 = {fmt_cap(best['C1'])}   C2 = {fmt_cap(best['C2'])}")
    print(f"    R1 = {fmt_res(best['R1_com'])}  (ideal: {fmt_res(best['R1_ideal'])})")
    print(f"    R2 = {fmt_res(best['R2_com'])}  (ideal: {fmt_res(best['R2_ideal'])})")
    print(f"    f0 real  = {best['f0_com']:.4f} Hz  (err: {best['err_f0']:.2f}%)")
    print(f"    zeta real= {best['zeta_com']:.4f}    (err: {best['err_zeta']:.2f}%)")