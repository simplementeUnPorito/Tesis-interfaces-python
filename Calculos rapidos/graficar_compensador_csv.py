# -*- coding: utf-8 -*-
"""
graficar_compensador_csv.py
Grafica K·|H_compensador| vs |H_ideal| agrupado por ζ similar (grupos de 6).
La referencia H_ideal usa la ζ promedio de cada grupo.
Dentro de cada grupo los candidatos se ordenan por error (menor primero, línea más gruesa).

════════════════════════════════════════════════════════════
 USO
════════════════════════════════════════════════════════════

  # Cargar TODOS los CSVs encontrados automáticamente:
  python graficar_compensador_csv.py

  # Un CSV específico:
  python graficar_compensador_csv.py compensador_optimo.csv

  # Varios CSVs a la vez:
  python graficar_compensador_csv.py compensador_resultados_todos.csv compensador_optimo.csv

  # Listar CSVs disponibles:
  python graficar_compensador_csv.py --list

  # Filtrar: mostrar solo los N candidatos con ζ más cercana al objetivo
  python graficar_compensador_csv.py --zeta 500
  python graficar_compensador_csv.py --zeta 500 --n 3   # solo los 3 más cercanos

  # Guardar los PNGs en subcarpeta graficos/ (por defecto NO guarda):
  python graficar_compensador_csv.py --guardar
  python graficar_compensador_csv.py --save              # alias de --guardar

  # Mostrar componentes de un rank específico (sin graficar):
  python graficar_compensador_csv.py compensador_full.csv --info 42
  python graficar_compensador_csv.py --info 7            # busca en todos los CSVs

  # Combinaciones:
  python graficar_compensador_csv.py compensador_optimo.csv --zeta 400 --guardar

════════════════════════════════════════════════════════════
"""
import sys, os, glob
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import csv
import matplotlib.pyplot as plt

# ════════════════════════════════════════════════════════════════════════════
# PARÁMETROS (deben coincidir con los scripts de cálculo)
# ════════════════════════════════════════════════════════════════════════════
f0_des      = 10
ZETA_GEO    = 0.25
ZETA_TARGET = 1000
omega0      = 2*np.pi*f0_des

A0_dB_bp    = 90;   A0_bp    = 10**(A0_dB_bp/20)
f_p_bp      = 8e6;  omega_p_bp  = 2*np.pi*f_p_bp
Rin_bp_amp  = 35e6

A0_dB_add   = 90;   A0_add   = 10**(A0_dB_add/20)
f_p_add     = 8e6;  omega_p_add = 2*np.pi*f_p_add
Rin_add     = 35e6

freqs_plot = np.logspace(-3, 6, 3000)

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE TRANSFERENCIA
# ════════════════════════════════════════════════════════════════════════════

def H_bp_real(R1, R2, C1, C2, freqs):
    s      = 1j*2*np.pi*freqs
    beta   = (s*R2*C1)/((s*R1*C1+1)*(s*R2*C2+1))
    z_load = (R2/Rin_bp_amp)/(s*R2*C2+1)
    nif    = (1+s/omega_p_bp)/A0_bp
    return -beta/(1+nif*(1+beta+z_load))

def H_compensador(R1, R2, C1, C2, Ru, Rbp, Rf, K_dB, freqs):
    """TF completa: BP real → sumador no-ideal → escalada por K_opt."""
    s    = 1j*2*np.pi*freqs
    nif  = (1+s/omega_p_add)/A0_add
    H_bp = H_bp_real(R1, R2, C1, C2, freqs)
    a    = Rf/Ru;  b = Rf/Rbp
    Av_u  = -a/(1+nif*(1+a+Rf/Rin_add))
    Av_bp = -b/(1+nif*(1+b+Rf/Rin_add))
    H_tot = Av_u + Av_bp*H_bp
    return 20*np.log10(np.abs(H_tot)) + K_dB   # magnitud en dB + corrección K

def H_ideal_dB(freqs, zeta_denom=None):
    """H_ideal con ζ₁ = zeta_denom (denominador). Si None usa ZETA_TARGET global."""
    z1 = zeta_denom if zeta_denom is not None else ZETA_TARGET
    s  = 1j*2*np.pi*freqs
    H  = (s**2+2*ZETA_GEO*omega0*s+omega0**2)/(s**2+2*z1*omega0*s+omega0**2)
    return 20*np.log10(np.abs(H))

# ════════════════════════════════════════════════════════════════════════════
# SELECCIÓN DEL CSV
# ════════════════════════════════════════════════════════════════════════════
script_dir = os.path.dirname(os.path.abspath(__file__))

# Patrón de CSVs que este script puede leer
_CSV_PATTERNS = [
    "compensador_resultados_*.csv",
    "compensador_optimo.csv",
    "compensador_todos_gpu.csv",
    "compensador_ceramicos_gpu.csv",
    "compensador_full.csv",
]

def _find_csvs():
    found = []
    for pat in _CSV_PATTERNS:
        found += glob.glob(os.path.join(script_dir, pat))
    return sorted(set(found), key=os.path.getmtime, reverse=True)

guardar = '--guardar' in sys.argv or '--save' in sys.argv

if '--list' in sys.argv:
    csvs = _find_csvs()
    print("CSVs disponibles (más reciente primero):")
    for p in csvs: print(f"  {os.path.basename(p)}")
    sys.exit(0)

# Si se pasan argumentos: usar esos CSVs; si no: cargar TODOS los encontrados
if len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
    csv_paths = []
    for arg in sys.argv[1:]:
        if arg.startswith('--'): break   # stop before flags like --zeta, --n
        p = arg if os.path.isabs(arg) else os.path.join(script_dir, arg)
        if os.path.exists(p):
            csv_paths.append(p)
        else:
            print(f"  AVISO: no existe {p}, ignorado")
else:
    csv_paths = _find_csvs()
    if not csv_paths:
        print("ERROR: No se encontró ningún CSV de compensador.")
        print("  Ejecutá primero calculoCompensadorSimple.py o calculoCompensadorOptimo.py")
        sys.exit(1)
    print(f"  Auto-cargando {len(csv_paths)} CSV(s) encontrados:")
    for p in csv_paths: print(f"    {os.path.basename(p)}")

# ════════════════════════════════════════════════════════════════════════════
# LEER TODOS LOS CSVs — compatible con formato nuevo y viejo
# ════════════════════════════════════════════════════════════════════════════

def _get(row, *keys, default=None, cast=float):
    """Lee el primer key que exista en el dict row."""
    for k in keys:
        if k in row and row[k] not in ('', None):
            try: return cast(row[k])
            except: pass
    return default

def _leer_csv(path):
    rows = []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        cols      = reader.fieldnames or []
        has_adder = 'Ru_ohm' in cols
        for row in reader:
            c = {
                'rank':    _get(row, 'rank', cast=int),
                'zeta':    _get(row, 'zeta'),
                'f0':      _get(row, 'f0_Hz'),
                'f0_err':  _get(row, 'f0_err_pct'),
                'err_rms': _get(row, 'err_rms_dB', 'err_banda_dB'),
                'K_opt_dB':_get(row, 'K_opt_dB', default=0.0),
                'C1':      _get(row, 'C1_F'),
                'C2':      _get(row, 'C2_F'),
                'C2_type': _get(row, 'C2_type', cast=str, default='S'),
                'R1':      _get(row, 'R1_ohm'),
                'R2_act':  _get(row, 'R2_act_ohm'),
                'R2_type': _get(row, 'R2_type', cast=str, default='S'),
                'Rref':    _get(row, 'Rref_ohm'),
                'source':  os.path.basename(path),
            }
            if has_adder:
                c.update({
                    'Ru':  _get(row, 'Ru_ohm'),
                    'Rbp': _get(row, 'Rbp_ohm'),
                    'Rf':  _get(row, 'Rf_ohm'),
                    'a':   _get(row, 'Rf_Ru',  'a_real'),
                    'b':   _get(row, 'Rf_Rbp', 'b_real'),
                })
            if c['R1'] and c['R2_act'] and c['C1'] and c['C2']:
                rows.append(c)
    return rows

# Cargar todos, deduplicar por (R1,R2,C1,C2,Ru,Rbp,Rf)
candidatos = []
seen_keys  = set()
has_adder  = False
for path in csv_paths:
    nuevos = _leer_csv(path)
    for c in nuevos:
        key = (c['R1'], c.get('R2_act'), c['C1'], c['C2'],
               c.get('Ru'), c.get('Rbp'), c.get('Rf'))
        if key not in seen_keys:
            seen_keys.add(key)
            candidatos.append(c)
            if c.get('Ru'): has_adder = True
    print(f"  {os.path.basename(path)}: {len(nuevos)} candidatos")

# ── Parsear argumentos ───────────────────────────────────────────────────
zeta_target = None
n_mostrar   = 5
info_rank   = None   # --info N: imprimir componentes del rank N y salir
for i, arg in enumerate(sys.argv[1:], 1):
    if arg.startswith('--zeta='):
        try: zeta_target = float(arg.split('=',1)[1])
        except: pass
    elif arg == '--zeta' and i < len(sys.argv)-1:
        try: zeta_target = float(sys.argv[i+1])
        except: pass
    elif arg.startswith('--n='):
        try: n_mostrar = int(arg.split('=',1)[1])
        except: pass
    elif arg == '--n' and i < len(sys.argv)-1:
        try: n_mostrar = int(sys.argv[i+1])
        except: pass
    elif arg.startswith('--info='):
        try: info_rank = int(arg.split('=',1)[1])
        except: pass
    elif arg == '--info' and i < len(sys.argv)-1:
        try: info_rank = int(sys.argv[i+1])
        except: pass

# ── --info: buscar rank y mostrar componentes ────────────────────────────
def _print_info(csv_paths_list, rank_target):
    for path in csv_paths_list:
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    if int(row.get('rank','')) == rank_target:
                        _print_row(row, rank_target, os.path.basename(path))
                        return
                except (ValueError, TypeError):
                    pass
    print(f"  Rank {rank_target} no encontrado en ningún CSV cargado.")

def _print_row(row, rank_target, source):
    def g(*keys):
        for k in keys:
            v = row.get(k,'')
            if v not in ('', None):
                return v
        return '—'
    def gf(*keys):
        v = g(*keys)
        try: return float(v)
        except: return None
    sep = '─' * 60
    print(f"\n{'═'*60}")
    print(f"  RANK {rank_target}  ·  {source}")
    print(f"{'═'*60}")
    zv = gf('zeta'); f0v = gf('f0_Hz'); f0e = gf('f0_err_pct')
    ev = gf('err_rms_dB','err_banda_dB'); kv = gf('K_opt_dB')
    print(f"  ζ        = {zv:.2f}" if zv else "  ζ        = —")
    print(f"  f0       = {f0v:.4f} Hz  (err {f0e:.2f}%)" if f0v else "  f0       = —")
    print(f"  Error    = {ev:.4f} dB" if ev is not None else "  Error    = —")
    print(f"  K_opt    = {kv:+.3f} dB" if kv is not None else "  K_opt    = —")
    print(f"  {sep}")
    print("  ── Pasabanda ────────────────────────────────────────────")
    c1v = gf('C1_F');  c2v = gf('C2_F')
    c2t = g('C2_type'); c2a = gf('C2_a_F'); c2b = gf('C2_b_F')
    r1v = gf('R1_ohm'); r2i = gf('R2_ideal_ohm'); r2a = gf('R2_act_ohm')
    r2t = g('R2_type'); r2e = g('R2_err_pct')
    print(f"  C1  = {fmt_cap(c1v)}" if c1v else "  C1  = —")
    c2_str = fmt_cap(c2v) if c2v else '—'
    if c2t == 'P' and c2a and c2b:
        c2_str += f"  (paralelo: {fmt_cap(c2a)} + {fmt_cap(c2b)})"
    elif c2t == 'E' and c2a and c2b:
        c2_str += f"  (serie: {fmt_cap(c2a)} -- {fmt_cap(c2b)})"
    print(f"  C2  = {c2_str}")
    print(f"  R1  = {fmt_res(r1v)}" if r1v else "  R1  = —")
    r2_str = fmt_res(r2a) if r2a else '—'
    if r2t == 'T':
        tra = gf('T_Ra','T1_Ra'); trb = gf('T_Rb','T1_Rb'); trc = gf('T_Rc','T1_Rc')
        if tra and trb and trc:
            r2_str += f"  [T: Ra={fmt_res(tra)} Rb={fmt_res(trb)} Rc={fmt_res(trc)}]"
    print(f"  R2  = {r2_str}  (ideal {fmt_res(r2i) if r2i else '—'}, err {r2e}%)")
    print("  ── Sumador ──────────────────────────────────────────────")
    ruv = gf('Ru_ohm'); rbpv = gf('Rbp_ohm'); rfv = gf('Rf_ohm')
    rf_ru = g('Rf_Ru','a_real'); rf_rbp = g('Rf_Rbp','b_real')
    rft = g('Rf_type')
    rf_str = fmt_res(rfv) if rfv else '—'
    if rft == 'T':
        rfa = gf('T_Ra'); rfb = gf('T_Rb'); rfc = gf('T_Rc')
        if rfa and rfb and rfc:
            rf_str += f"  [T: Ra={fmt_res(rfa)} Rb={fmt_res(rfb)} Rc={fmt_res(rfc)}]"
    print(f"  Ru  = {fmt_res(ruv) if ruv else '—'}    (Rf/Ru  = {rf_ru})")
    print(f"  Rbp = {fmt_res(rbpv) if rbpv else '—'}    (Rf/Rbp = {rf_rbp})")
    print(f"  Rf  = {rf_str}")
    print(f"{'═'*60}\n")

# Ordenar por ζ (siempre)
candidatos.sort(key=lambda x: x['zeta'] or 0)

# Filtrar si se pidió un objetivo de ζ
if zeta_target is not None:
    # 1. Pool: todos dentro de ±5% de zeta_target (o al menos 200 más cercanos)
    tol  = max(zeta_target * 0.05, 10.0)
    pool = [c for c in candidatos if abs((c['zeta'] or 0) - zeta_target) <= tol]
    if len(pool) < n_mostrar:
        pool = sorted(candidatos,
                      key=lambda x: abs((x['zeta'] or 0) - zeta_target))[:max(n_mostrar*20, 200)]
    # 2. De ese pool, los N con menor error
    candidatos = sorted(pool, key=lambda x: x['err_rms'] or 9e9)[:n_mostrar]
    candidatos.sort(key=lambda x: x['zeta'] or 0)
    print(f"  Filtrado: {len(candidatos)} mejores (menor error) con ζ≈{zeta_target:.0f} "
          f"(pool={len(pool)} candidatos dentro de ±{tol:.0f})")
else:
    print(f"  Mostrando todos ({len(candidatos)}) — pasá --zeta N para filtrar")

titulo_csv = (os.path.basename(csv_paths[0]) if len(csv_paths) == 1
              else f"{len(csv_paths)} CSVs combinados")
if zeta_target is not None:
    titulo_csv += f"  |  filtro ζ≈{zeta_target:.0f}"
if candidatos:
    print(f"  Total a graficar: {len(candidatos)}  |  "
          f"Adder: {'sí' if has_adder else 'solo BP'}  |  "
          f"ζ {min(c['zeta'] for c in candidatos):.0f}"
          f"–{max(c['zeta'] for c in candidatos):.0f}")

# ════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ════════════════════════════════════════════════════════════════════════════

def fmt_cap(c):
    if c >= 1e-6: return f"{c*1e6:.3g}uF"
    if c >= 1e-9: return f"{c*1e9:.3g}nF"
    return f"{c*1e12:.3g}pF"

def fmt_res(r):
    if r >= 1e6: return f"{r/1e6:.3g}Mohm"
    if r >= 1e3: return f"{r/1e3:.3g}kohm"
    return f"{r:.3g}ohm"

if info_rank is not None:
    _print_info(csv_paths, info_rank)
    sys.exit(0)

COLORES = ['royalblue','forestgreen','firebrick','darkorange',
           'purple','teal','brown','crimson']   # max 6+2 por grupo

def _mag_candidato(r):
    R1, R2 = r['R1'], r['R2_act']
    C1, C2 = r['C1'], r['C2']
    if has_adder and r.get('Ru') and r.get('Rbp') and r.get('Rf'):
        return H_compensador(R1, R2, C1, C2, r['Ru'], r['Rbp'], r['Rf'],
                             r['K_opt_dB'], freqs_plot)
    return 20*np.log10(np.abs(H_bp_real(R1, R2, C1, C2, freqs_plot)))

def _lbl(r, local_rank):
    src      = r.get('source','').replace('compensador_','').replace('.csv','')
    csv_rank = r['rank'] if r.get('rank') is not None else '—'
    err      = r.get('err_rms') or 0.0
    return f"#{local_rank}  ζ={r['zeta']:.0f}  err={err:.3f}dB  [{src} rank={csv_rank}]"

# ════════════════════════════════════════════════════════════════════════════
# Agrupar por ζ similar (chunks de 6 ordenados por ζ)
# Dentro de cada grupo: ordenar por err_rms ascendente (mejor primero)
# ════════════════════════════════════════════════════════════════════════════
GRUPO_SIZE = 6
# candidatos ya están ordenados por zeta
grupos = [candidatos[i:i+GRUPO_SIZE] for i in range(0, len(candidatos), GRUPO_SIZE)]
# ordenar cada grupo por error ascendente
grupos = [sorted(g, key=lambda r: r['err_rms'] or 9e9) for g in grupos]

figs_guardadas = []
for gi, grupo in enumerate(grupos):
    z_min  = min(r['zeta'] for r in grupo)
    z_max  = max(r['zeta'] for r in grupo)
    z_prom = sum(r['zeta'] for r in grupo) / len(grupo)
    # H_ideal con la ζ promedio del grupo como referencia del denominador
    mag_id = H_ideal_dB(freqs_plot, zeta_denom=z_prom)

    subtitulo = (f"Grupo {gi+1}/{len(grupos)}  ·  "
                 f"ζ {z_min:.0f}–{z_max:.0f}  ·  {titulo_csv}")

    fig, (ax_mag, ax_err) = plt.subplots(2, 1, figsize=(13, 9), sharex=True)

    ax_mag.semilogx(freqs_plot, mag_id, 'k--', lw=2.0,
                    label=f'H_ideal  (ζ₁={z_prom:.0f})')
    for local_rank, r in enumerate(grupo, 1):
        col = COLORES[(local_rank-1) % len(COLORES)]
        mag = _mag_candidato(r)
        lbl = _lbl(r, local_rank)
        lw  = 2.2 if local_rank == 1 else 1.5   # el mejor más grueso
        ax_mag.semilogx(freqs_plot, mag,         color=col, lw=lw, label=lbl)
        ax_err.semilogx(freqs_plot, mag - mag_id, color=col, lw=lw, label=lbl)

    ax_mag.axvline(f0_des, color='gray', ls=':', lw=0.8)
    ax_mag.set_ylabel('Magnitud (dB)')
    ax_mag.set_title(f'K·|H_comp| vs H_ideal  —  {subtitulo}')
    ax_mag.legend(loc='lower right', fontsize=8)
    ax_mag.grid(True, which='both', ls=':', alpha=0.4)
    ax_mag.set_xlim([freqs_plot[0], freqs_plot[-1]])

    ax_err.axhline(0, color='black', lw=0.8)
    ax_err.axvline(f0_des, color='gray', ls=':', lw=0.8)
    ax_err.set_xlabel('Frecuencia (Hz)')
    ax_err.set_ylabel('Error (dB)')
    ax_err.set_title(f'Error = K·|H_comp| − |H_ideal(ζ₁={z_prom:.0f})|  '
                     f'(0 dB = perfecto  ·  #1 = menor error  ·  ζ₀={ZETA_GEO})')
    ax_err.legend(loc='upper right', fontsize=8)
    ax_err.grid(True, which='both', ls=':', alpha=0.4)

    fig.tight_layout()
    if guardar:
        out_dir = os.path.join(script_dir, "graficos")
        os.makedirs(out_dir, exist_ok=True)
        fname = os.path.join(out_dir, f"compensador_grupo_{gi+1:02d}.png")
        fig.savefig(fname, dpi=150)
        figs_guardadas.append(fname)
    resumen = [f"#{k+1} z={r['zeta']:.0f} e={r['err_rms']:.3f}" for k, r in enumerate(grupo)]
    print(f"  Grupo {gi+1}: {resumen}")

if guardar and figs_guardadas:
    print(f"\n  {len(figs_guardadas)} figura(s) guardada(s):")
    for f in figs_guardadas: print(f"    {os.path.basename(f)}")
else:
    print(f"\n  (Pasá --guardar para guardar los PNGs)")
plt.show()
