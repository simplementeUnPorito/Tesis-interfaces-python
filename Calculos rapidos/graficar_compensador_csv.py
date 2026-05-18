# -*- coding: utf-8 -*-
"""
Lee compensador_resultados.csv y grafica TF real vs ideal para cada candidato.
Uso: python graficar_compensador_csv.py
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import csv
import os
import matplotlib.pyplot as plt

# ── Parámetros del op-amp (deben coincidir con calculoCompensadorSimple.py) ──
A0_dB   = 90
A0      = 10**(A0_dB / 20.0)
f_p     = 8e6
omega_p = 2*np.pi * f_p
Rin     = 35e6
f0_des  = 10  # Hz

def fmt_cap(c):
    if c >= 1e-6: return f"{c*1e6:.2f}uF"
    if c >= 1e-9: return f"{c*1e9:.2f}nF"
    return f"{c*1e12:.2f}pF"

def fmt_res(r):
    if r >= 1e6: return f"{r/1e6:.3f}Mohm"
    if r >= 1e3: return f"{r/1e3:.3f}kohm"
    return f"{r:.1f}ohm"

def H_ideal(R1, R2, C1, C2, freqs_hz):
    s = 1j * 2*np.pi * freqs_hz
    beta = (s*R2*C1) / ((s*R1*C1 + 1) * (s*R2*C2 + 1))
    return -beta

def H_real(R1, R2, C1, C2, freqs_hz):
    s      = 1j * 2*np.pi * freqs_hz
    beta   = (s*R2*C1) / ((s*R1*C1 + 1) * (s*R2*C2 + 1))
    z_load = (R2 / Rin) / (s*R2*C2 + 1)
    nif    = (1 + s/omega_p) / A0
    return -beta / (1 + nif * (1 + beta + z_load))

# ── Leer CSV ─────────────────────────────────────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))
csv_path   = os.path.join(script_dir, "compensador_resultados.csv")

if not os.path.exists(csv_path):
    print(f"ERROR: No se encontró {csv_path}")
    print("Ejecutá primero calculoCompensadorSimple.py")
    sys.exit(1)

candidatos = []
with open(csv_path, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        candidatos.append({
            'rank':     int(row['rank']),
            'C1':       float(row['C1_F']),
            'C2':       float(row['C2_F']),
            'R1':       float(row['R1_ohm']),
            'R2_ideal': float(row['R2_ideal_ohm']),
            'R2_act':   float(row['R2_act_ohm']),
            'f0':       float(row['f0_Hz']),
            'zeta':     float(row['zeta']),
            'err_rms':  float(row['err_rms_dB']),
            'R2_type':  row['R2_type'],
        })

print(f"  {len(candidatos)} candidatos leídos de {csv_path}")

# ── Frecuencias de plot ───────────────────────────────────────────────────
freqs_plot = np.logspace(-3, 6, 3000)

colores = ['royalblue', 'forestgreen', 'firebrick', 'darkorange',
           'purple', 'teal', 'brown', 'crimson', 'gold', 'navy',
           'olive', 'deeppink', 'slategray', 'chocolate', 'indigo']

# ── Figura 1: todos los candidatos, magnitud ─────────────────────────────
fig1, ax1 = plt.subplots(figsize=(15, 8))
for r in candidatos:
    col = colores[(r['rank']-1) % len(colores)]
    R1, R2, C1, C2 = r['R1'], r['R2_act'], r['C1'], r['C2']
    Hi = 20*np.log10(np.abs(H_ideal(R1, R2, C1, C2, freqs_plot)))
    Hr = 20*np.log10(np.abs(H_real( R1, R2, C1, C2, freqs_plot)))
    lbl = (f"#{r['rank']} ζ={r['zeta']:.0f} errRMS={r['err_rms']:.3f}dB"
           f"\n  R1={fmt_res(R1)} C1={fmt_cap(C1)} C2={fmt_cap(C2)} R2={fmt_res(R2)}")
    ax1.semilogx(freqs_plot, Hi, color=col, ls='--', lw=1.0, alpha=0.5)
    ax1.semilogx(freqs_plot, Hr, color=col, ls='-',  lw=1.8, label=lbl)

ax1.axvline(f0_des, color='gray', ls=':', lw=1.0, label=f'f0={f0_des}Hz')
ax1.set_xlabel('Frecuencia (Hz)'); ax1.set_ylabel('Magnitud (dB)')
ax1.set_title(f'TF real (—) vs ideal (- -)  |  A0={A0_dB}dB  fp={f_p/1e6:.0f}MHz  Rin={fmt_res(Rin)}')
ax1.legend(loc='lower right', fontsize=6, ncol=2)
ax1.grid(True, which='both', ls=':', alpha=0.4)
ax1.set_xlim([freqs_plot[0], freqs_plot[-1]])
fig1.tight_layout()
fig1.savefig(os.path.join(script_dir, "compensador_todos_mag.png"), dpi=150)

# ── Figura 2: error dB por candidato ─────────────────────────────────────
fig2, ax2 = plt.subplots(figsize=(15, 6))
for r in candidatos:
    col = colores[(r['rank']-1) % len(colores)]
    R1, R2, C1, C2 = r['R1'], r['R2_act'], r['C1'], r['C2']
    Hi = np.abs(H_ideal(R1, R2, C1, C2, freqs_plot))
    Hr = np.abs(H_real( R1, R2, C1, C2, freqs_plot))
    mask = (Hi > 1e-30) & (Hr > 1e-30)
    err = np.zeros_like(freqs_plot)
    err[mask] = 20*np.log10(Hr[mask]) - 20*np.log10(Hi[mask])
    ax2.semilogx(freqs_plot, err, color=col, lw=1.5,
                 label=f"#{r['rank']} ζ={r['zeta']:.0f}")

ax2.axhline(0, color='black', ls='-', lw=0.8)
ax2.axvline(f0_des, color='gray', ls=':', lw=1.0)
ax2.set_xlabel('Frecuencia (Hz)'); ax2.set_ylabel('Error (dB)')
ax2.set_title('Error = 20log|H_real / H_ideal|  (0 dB = coincidencia perfecta)')
ax2.legend(fontsize=7, ncol=3); ax2.grid(True, which='both', ls=':', alpha=0.4)
ax2.set_xlim([freqs_plot[0], freqs_plot[-1]])
fig2.tight_layout()
fig2.savefig(os.path.join(script_dir, "compensador_todos_error.png"), dpi=150)

# ── Figura 3: subplots individuales (real vs ideal + error) ──────────────
n = len(candidatos)
ncols = 3
nrows = int(np.ceil(n / ncols))
fig3, axes = plt.subplots(nrows, ncols, figsize=(6*ncols, 4*nrows))
axes = np.array(axes).flatten()

for i, r in enumerate(candidatos):
    ax = axes[i]
    col = colores[i % len(colores)]
    R1, R2, C1, C2 = r['R1'], r['R2_act'], r['C1'], r['C2']
    Hi = 20*np.log10(np.abs(H_ideal(R1, R2, C1, C2, freqs_plot)))
    Hr = 20*np.log10(np.abs(H_real( R1, R2, C1, C2, freqs_plot)))
    ax.semilogx(freqs_plot, Hi, 'k--', lw=1.0, alpha=0.5, label='ideal')
    ax.semilogx(freqs_plot, Hr, color=col, lw=1.8, label='real')
    ax.axvline(f0_des, color='gray', ls=':', lw=0.8)
    ax.set_title(
        f"#{r['rank']} ζ={r['zeta']:.0f}  errRMS={r['err_rms']:.3f}dB\n"
        f"R1={fmt_res(R1)} C1={fmt_cap(C1)}\n"
        f"C2={fmt_cap(C2)} R2={fmt_res(R2)}",
        fontsize=7
    )
    ax.legend(fontsize=6); ax.grid(True, which='both', ls=':', alpha=0.4)
    ax.set_xlabel('Hz', fontsize=7); ax.set_ylabel('dB', fontsize=7)
    ax.tick_params(labelsize=6)
    ax.set_xlim([freqs_plot[0], freqs_plot[-1]])

for j in range(i+1, len(axes)):
    axes[j].set_visible(False)

fig3.suptitle(f'Todos los candidatos — A0={A0_dB}dB fp={f_p/1e6:.0f}MHz', fontsize=10)
fig3.tight_layout()
fig3.savefig(os.path.join(script_dir, "compensador_subplots.png"), dpi=120)

print("  Guardados: compensador_todos_mag.png  compensador_todos_error.png  compensador_subplots.png")
plt.show()
