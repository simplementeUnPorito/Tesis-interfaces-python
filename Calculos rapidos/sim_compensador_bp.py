# -*- coding: utf-8 -*-
"""
sim_compensador_bp.py
Simulacion en frecuencia del compensador geofono:
  etapa BP  +  sumador inversor con T-red en R2
"""
import numpy as np
import matplotlib.pyplot as plt

# ════════════════════════════════════════════════════════════════════════════
# GEOFONO
# ════════════════════════════════════════════════════════════════════════════
fn  = 10.0      # Hz
hi  = 0.25
G   = 28.8      # V·s/m
m   = 0.011     # kg
R_s = 375.0     # Ohm
wn  = 2*np.pi*fn

# H_geo(s) = G · wn·s / (s² + 2·hi·wn·s + wn²)
# (N = [0, wn, 0]  D = [1, 2·hi·wn, wn²]  según MATLAB, con G como ganancia)

# ════════════════════════════════════════════════════════════════════════════
# OBJETIVO
# ════════════════════════════════════════════════════════════════════════════
f0_des      = fn
ZETA_GEO    = hi
ZETA_TARGET = 1000.0
omega0      = wn

# ════════════════════════════════════════════════════════════════════════════
# COMPONENTES
# ════════════════════════════════════════════════════════════════════════════
# ── Rank 10 del optimizador (zeta=943, errRMS=0.132 dB) ─────────────────────
R1_bp        = 30000.0
Ra_bp        = 1500.0
Rb_bp        = 8200.0
Rc_bp        = 62000.0
R2_bp        = Ra_bp + Rb_bp + Ra_bp*Rb_bp/Rc_bp   # T-red -> ~9898 Ohm

C1_bp        = 0.001                                # 1000 uF
C2_bp        = 8.53e-10                             # 853 pF (33pF + 820pF en paralelo)

Rin_bp_adder = 33000.0   # Rbp
Rin_u_adder  = 100000.0  # Ru
Rf_add       = 100000.0
Cf_add       = 0.0        # sin cap HF (no incluido en optimizacion)
Rref_add     = 7500.0     # bias compensation (no afecta respuesta AC)

# ════════════════════════════════════════════════════════════════════════════
# PARAMETROS DEL BP
# ════════════════════════════════════════════════════════════════════════════
tau1   = R1_bp * C1_bp
tau2   = R2_bp * C2_bp
f0_bp  = 1.0 / (2*np.pi*np.sqrt(tau1*tau2))
zeta   = (tau1 + tau2) / (2*np.sqrt(tau1*tau2))
gpeak  = R2_bp*C1_bp / (R1_bp*C1_bp + R2_bp*C2_bp)
f_pole1 = 1.0/(2*np.pi*tau1)
f_pole2 = 1.0/(2*np.pi*tau2)
f_add_pole = (1.0/(2*np.pi*Rf_add*Cf_add)) if Cf_add > 0 else np.inf

print(f"  R2_T   = {R2_bp/1e3:.2f} kohm")
print(f"  f0_BP  = {f0_bp:.3f} Hz")
print(f"  zeta   = {zeta:.1f}")
print(f"  gpeak  = {gpeak:.5f}")
print(f"  f_p1   = {f_pole1:.4f} Hz   (polo BP baja)")
print(f"  f_p2   = {f_pole2/1e3:.2f} kHz  (polo BP alta)")
if np.isfinite(f_add_pole):
    print(f"  f_add  = {f_add_pole/1e3:.2f} kHz  (polo adder)")
else:
    print(f"  f_add  = inf  (sin Cf)")
print(f"  a=Rf/Ru  = {Rf_add/Rin_u_adder:.4f}")
print(f"  b=Rf/Rbp = {Rf_add/Rin_bp_adder:.4f}")

# ════════════════════════════════════════════════════════════════════════════
# RESPUESTA EN FRECUENCIA
# ════════════════════════════════════════════════════════════════════════════
freqs = np.logspace(-2, 5, 3000)
s     = 1j * 2*np.pi * freqs

# BP (opamp ideal)
H_BP = -(s*R2_bp*C1_bp) / ((1 + s*R1_bp*C1_bp) * (1 + s*R2_bp*C2_bp))

# Adder: Zf = Rf || Cf
Zf     = Rf_add / (1 + s*Rf_add*Cf_add)
Av_u   = -Zf / Rin_u_adder
Av_bp  = -Zf / Rin_bp_adder

H_total = Av_u + Av_bp * H_BP

# Ideal (factor de correccion de amortiguamiento)
H_ideal = (s**2 + 2*ZETA_GEO*omega0*s + omega0**2) / \
          (s**2 + 2*ZETA_TARGET*omega0*s + omega0**2)

# Geofono
H_geo = G * wn * s / (s**2 + 2*hi*wn*s + wn**2)

# Geofono ideal (con amortiguamiento target) — respuesta objetivo cascada
H_geo_ideal = G * wn * s / (s**2 + 2*ZETA_TARGET*wn*s + wn**2)

# Cascada: geofono * compensador
H_cascade = H_geo * H_total

# K optimo compensador solo (min error RMS vs H_ideal)
mag_t  = np.abs(H_total)
mag_id = np.abs(H_ideal)
mask   = (mag_t > 1e-30) & (mag_id > 1e-30)
diff   = 20*np.log10(mag_id[mask]) - 20*np.log10(mag_t[mask])
K_dB   = float(np.mean(diff))
K_lin  = 10**(K_dB/20)
err_rms = float(np.sqrt(np.mean((diff - K_dB)**2)))

# K optimo cascada (min error RMS vs H_geo_ideal)
mag_c  = np.abs(H_cascade)
mag_gi = np.abs(H_geo_ideal)
mask_c = (mag_c > 1e-30) & (mag_gi > 1e-30)
diff_c = 20*np.log10(mag_gi[mask_c]) - 20*np.log10(mag_c[mask_c])
Kc_dB  = float(np.mean(diff_c))
Kc_lin = 10**(Kc_dB/20)
err_rms_c = float(np.sqrt(np.mean((diff_c - Kc_dB)**2)))

print(f"\n  K_opt  = {K_dB:+.2f} dB  ({K_lin:.5f})")
print(f"  errRMS = {err_rms:.4f} dB")

# ════════════════════════════════════════════════════════════════════════════
# PLOT
# ════════════════════════════════════════════════════════════════════════════
print(f"  K_comp = {K_dB:+.2f} dB  errRMS_comp = {err_rms:.4f} dB")
print(f"  K_casc = {Kc_dB:+.2f} dB  errRMS_casc = {err_rms_c:.4f} dB")

fig, axes = plt.subplots(2, 2, figsize=(15, 9), sharex=True)
fig.suptitle(
    f'Compensador geofono  -  f0={f0_des}Hz  hi={ZETA_GEO}  zeta_target={ZETA_TARGET:.0f}  '
    f'G={G}V·s/m',
    fontsize=12)

(ax1, ax3), (ax2, ax4) = axes

# ── Columna izquierda: compensador solo ─────────────────────────────────────
ax1.set_title('Compensador (BP + Adder)', fontsize=10)
ax1.semilogx(freqs, 20*np.log10(np.abs(H_ideal)),
             'k', lw=2, ls='--', label='H_ideal (objetivo)', zorder=5)
ax1.semilogx(freqs, 20*np.log10(np.abs(H_BP)),
             'steelblue', lw=1.5,
             label=f'H_BP  zeta={zeta:.0f}  gpeak={gpeak:.4f}')
ax1.semilogx(freqs, 20*np.log10(np.abs(H_total)),
             'darkorange', lw=1.5, label='H_total = Av_u + Av_bp·H_BP')
ax1.semilogx(freqs, 20*np.log10(np.abs(H_total * K_lin)),
             'firebrick', lw=2, ls='--',
             label=f'K·H_total  K={K_dB:+.1f}dB  errRMS={err_rms:.2f}dB')
ax1.axvline(f0_bp, color='gray', lw=0.8, ls=':', alpha=0.7, label=f'f0={f0_bp:.2f}Hz')
if np.isfinite(f_add_pole):
    ax1.axvline(f_add_pole, color='purple', lw=0.8, ls=':', alpha=0.6,
                label=f'polo adder={f_add_pole/1e3:.1f}kHz')
ax1.set_ylabel('Magnitud (dB)')
ax1.legend(fontsize=7.5, loc='lower left')
ax1.grid(True, which='both', alpha=0.35)
ax1.set_ylim([-80, 20])

ax2.semilogx(freqs, np.angle(H_ideal, deg=True),      'k',          lw=2,   ls='--', label='H_ideal')
ax2.semilogx(freqs, np.angle(H_BP, deg=True),          'steelblue',  lw=1.5,          label='H_BP')
ax2.semilogx(freqs, np.angle(H_total, deg=True),       'darkorange', lw=1.5,          label='H_total')
ax2.semilogx(freqs, np.angle(H_total*K_lin, deg=True), 'firebrick',  lw=2,   ls='--', label='K·H_total')
ax2.set_ylabel('Fase (°)')
ax2.set_xlabel('Frecuencia (Hz)')
ax2.legend(fontsize=7.5)
ax2.grid(True, which='both', alpha=0.35)
ax2.set_ylim([-200, 200])
ax2.axhline(0, color='gray', lw=0.5)

# ── Columna derecha: cascada geofono + compensador ──────────────────────────
ax3.set_title('Cascada: Geofono × Compensador', fontsize=10)
ax3.semilogx(freqs, 20*np.log10(np.abs(H_geo)),
             'mediumseagreen', lw=1.5, label=f'H_geo  (hi={hi})', zorder=3)
ax3.semilogx(freqs, 20*np.log10(np.abs(H_geo_ideal)),
             'k', lw=2, ls='--', label=f'H_geo_ideal  (hi={ZETA_TARGET:.0f})', zorder=5)
ax3.semilogx(freqs, 20*np.log10(np.abs(H_cascade)),
             'darkorange', lw=1.5, label='H_geo x H_total')
ax3.semilogx(freqs, 20*np.log10(np.abs(H_cascade * Kc_lin)),
             'firebrick', lw=2, ls='--',
             label=f'K·H_cascade  K={Kc_dB:+.1f}dB  errRMS={err_rms_c:.2f}dB')
ax3.axvline(f0_bp, color='gray', lw=0.8, ls=':', alpha=0.7, label=f'f0={f0_bp:.2f}Hz')
ax3.set_ylabel('Magnitud (dB)')
ax3.legend(fontsize=7.5, loc='lower left')
ax3.grid(True, which='both', alpha=0.35)

ax4.semilogx(freqs, np.angle(H_geo, deg=True),           'mediumseagreen', lw=1.5, label='H_geo')
ax4.semilogx(freqs, np.angle(H_geo_ideal, deg=True),     'k',  lw=2, ls='--', label='H_geo_ideal')
ax4.semilogx(freqs, np.angle(H_cascade, deg=True),       'darkorange', lw=1.5, label='H_geo x H_total')
ax4.semilogx(freqs, np.angle(H_cascade*Kc_lin, deg=True),'firebrick', lw=2, ls='--', label='K·H_cascade')
ax4.set_ylabel('Fase (°)')
ax4.set_xlabel('Frecuencia (Hz)')
ax4.legend(fontsize=7.5)
ax4.grid(True, which='both', alpha=0.35)
ax4.set_ylim([-200, 200])
ax4.axhline(0, color='gray', lw=0.5)

# Tabla de componentes
info = (
    f"BP:   R1={R1_bp/1e3:.0f}kΩ   "
    f"T-red: Ra={Ra_bp/1e3:.0f}k Rb={Rb_bp/1e3:.0f}k Rc={Rc_bp/1e3:.1f}k -> R2={R2_bp/1e3:.1f}kΩ\n"
    f"      C1={C1_bp*1e6:.0f}µF   C2={C2_bp*1e12:.2f}pF\n"
    f"ADD:  Ru={Rin_u_adder/1e3:.1f}kΩ   Rbp={Rin_bp_adder/1e3:.1f}kΩ   "
    f"Rf={Rf_add/1e3:.1f}kΩ   Cf={Cf_add*1e9:.2f}nF"
)
ax1.annotate(info, xy=(0.01, 0.97), xycoords='axes fraction',
             va='top', fontsize=7, family='monospace',
             bbox=dict(boxstyle='round', fc='white', alpha=0.85))

plt.tight_layout()
out = 'sim_compensador_bp.png'
plt.savefig(out, dpi=150, bbox_inches='tight')
print(f"\n  Grafico guardado: {out}")
plt.show()
