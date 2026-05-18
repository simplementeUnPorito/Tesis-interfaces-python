# -*- coding: utf-8 -*-
"""
calculoCompensadorFull.py
Búsqueda exhaustiva — GPU (CUDA) + multicore CPU.

Recorre TODAS las combinaciones comerciales (R1, C1, C2 con paralelo/serie),
R2 determinado analíticamente de la condición f0, adder resuelto por lstsq.
Guarda TODOS los candidatos que superan el filtro de banda de paso, ordenados por ζ.

Criterio de filtrado:
  Error en la banda de paso [ω₀(ζ-√(ζ²-1)), ω₀(ζ+√(ζ²-1))] entre K·|H_total|
  y |H_ideal(ζ₁=ζ_cand)| < MAX_ERR_BANDA_dB.
  La referencia usa la ζ real del candidato, no un ZETA_TARGET fijo.
"""
import sys, os, time, csv
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import warnings
warnings.filterwarnings('ignore')
from concurrent.futures import ProcessPoolExecutor, as_completed

# ── GPU ──────────────────────────────────────────────────────────────────────
BACKEND = 'cpu'; device = None; torch = None
try:
    import torch as _torch
    torch = _torch
    if torch.cuda.is_available():
        device  = torch.device('cuda')
        BACKEND = 'cuda'
        _gn = torch.cuda.get_device_name(0)
        _gm = torch.cuda.get_device_properties(0).total_memory/1e9
        print(f"  GPU: {_gn}  ({_gm:.1f} GB)", flush=True)
    else:
        device  = torch.device('cpu')
        BACKEND = 'cpu-torch'
        print("  GPU no disponible, usando CPU+torch", flush=True)
except ImportError:
    print("  torch no instalado — pip install torch --index-url https://download.pytorch.org/whl/cu121", flush=True)

try:
    from tqdm import tqdm
except ImportError:
    class tqdm:
        def __init__(self, it=None, total=None, desc='', unit='it', ncols=None, **kw):
            self._it=list(it) if it is not None else []; self._tot=total or len(self._it)
            self._n=0; self._t0=time.time(); self._desc=desc; self._last=-1
        def __iter__(self):
            for x in self._it:
                yield x; self._n+=1
                pct=int(100*self._n/self._tot) if self._tot else 0
                if pct!=self._last and pct%5==0:
                    el=time.time()-self._t0; eta=el/self._n*(self._tot-self._n) if self._n else 0
                    print(f"\r  {self._desc}: {pct}% [{self._n}/{self._tot}] "
                          f"elapsed {el:.0f}s ETA {eta:.0f}s  ",end='',flush=True); self._last=pct
            print(flush=True)
        def set_postfix_str(self,s): pass
        def update(self,n=1): pass
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self,*a): pass

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ════════════════════════════════════════════════════════════════════════════

# ── Geófono ──────────────────────────────────────────────────────────────────
f0_des      = 10
ZETA_GEO    = 0.25
omega0      = 2*np.pi*f0_des

# ── Op-amp BP ─────────────────────────────────────────────────────────────────
A0_dB_bp    = 90;  A0_bp    = 10**(A0_dB_bp/20.0)
f_p_bp      = 8e6; omega_p_bp  = 2*np.pi*f_p_bp
Rin_bp_amp  = 35e6

# ── Op-amp Adder ──────────────────────────────────────────────────────────────
A0_dB_add   = 90;  A0_add   = 10**(A0_dB_add/20.0)
f_p_add     = 8e6; omega_p_add = 2*np.pi*f_p_add
Rin_add     = 35e6

# ── Rangos de búsqueda ───────────────────────────────────────────────────────
R_min_bp    = 1e3
R_max_bp    = 1e6
R_min_add   = 1e3
R_max_add   = 1e6
F0_TOL_PCT  = 10.0   # % — tolerancia f0 después de cuantizar R2

# ── Filtro de banda de paso ───────────────────────────────────────────────────
MAX_ERR_BANDA_dB = 2.0   # umbral de error en banda de paso (dB)

# ── GPU / CPU ─────────────────────────────────────────────────────────────────
N_CPU_WORKERS  = os.cpu_count() or 1
GPU_CHUNK      = 50_000    # candidatos por chunk GPU (4GB VRAM → ~50k safe)
CPU_CHUNK      = 5_000     # candidatos por chunk para adder CPU paralelo

# ── Frecuencias de evaluación (más densas en zona de interés) ─────────────────
FREQS_EVAL = np.logspace(np.log10(f0_des/2000), np.log10(1e5), 1200)

# ════════════════════════════════════════════════════════════════════════════
# VALORES COMERCIALES
# ════════════════════════════════════════════════════════════════════════════
RES_COMERCIALES = sorted([
    1e3,1.2e3,1.5e3,1.8e3,2e3,2.2e3,2.4e3,2.7e3,3e3,3.3e3,3.6e3,3.9e3,
    4.3e3,4.7e3,5.1e3,5.6e3,6.2e3,6.8e3,7.5e3,8.2e3,9.1e3,
    10e3,12e3,15e3,18e3,20e3,22e3,24e3,27e3,30e3,33e3,36e3,39e3,
    43e3,47e3,51e3,56e3,62e3,68e3,75e3,82e3,91e3,
    100e3,120e3,150e3,180e3,200e3,220e3,240e3,270e3,300e3,330e3,360e3,390e3,
    430e3,470e3,510e3,560e3,620e3,680e3,750e3,820e3,910e3,1e6
])
CAP_COMERCIALES = sorted(set([
    1e-12,2e-12,5e-12,10e-12,12e-12,15e-12,18e-12,22e-12,27e-12,30e-12,
    33e-12,39e-12,47e-12,50e-12,56e-12,68e-12,82e-12,100e-12,120e-12,
    150e-12,200e-12,330e-12,470e-12,560e-12,820e-12,
    1e-9,2e-9,3.3e-9,3.9e-9,6.9e-9,10e-9,15e-9,20e-9,33e-9,47e-9,100e-9,
    0.1e-6,0.22e-6,0.47e-6,1e-6,2.2e-6,3.3e-6,4.7e-6,
    10e-6,22e-6,33e-6,44e-6,47e-6,100e-6,220e-6,330e-6,470e-6,680e-6,1000e-6
]))

_RES_BP      = [r for r in RES_COMERCIALES if R_min_bp  <= r <= R_max_bp]
_RES_ADD     = [r for r in RES_COMERCIALES if R_min_add <= r <= R_max_add]
_RES_ARR_ALL = np.array(RES_COMERCIALES, dtype=np.float64)
_RES_ADD_ARR = np.array(_RES_ADD, dtype=np.float64)
_CAP_ARR     = np.array(CAP_COMERCIALES, dtype=np.float64)

def res_comercial(v):
    return float(RES_COMERCIALES[int(np.argmin(np.abs(_RES_ARR_ALL - v)))])

def res_comercial_batch(vals):
    vals = np.asarray(vals, dtype=np.float64)
    idx  = np.searchsorted(_RES_ARR_ALL, vals, side='left').clip(0, len(_RES_ARR_ALL)-1)
    il   = np.maximum(idx-1, 0)
    return _RES_ARR_ALL[np.where(np.abs(_RES_ARR_ALL[il]-vals) < np.abs(_RES_ARR_ALL[idx]-vals), il, idx)]

def fmt_cap(c):
    if c>=1e-6: return f"{c*1e6:.3g}uF"
    if c>=1e-9: return f"{c*1e9:.3g}nF"
    return f"{c*1e12:.3g}pF"

def fmt_res(r):
    if r>=1e6: return f"{r/1e6:.3g}Mohm"
    if r>=1e3: return f"{r/1e3:.3g}kohm"
    return f"{r:.3g}ohm"

# ════════════════════════════════════════════════════════════════════════════
# T-REDES R2
# ════════════════════════════════════════════════════════════════════════════
_Ra_max = max(_RES_BP); _Rc_min = min(_RES_BP)
R2_max_tred = 2*_Ra_max + _Ra_max**2/_Rc_min
print("  Precomputando T-redes R2...", flush=True)
_t0 = time.time()
_tred_entries = sorted(
    [(_Ra+_Rb+_Ra*_Rb/_Rc, _Ra, _Rb, _Rc)
     for _Ra in _RES_BP for _Rb in _RES_BP for _Rc in _RES_BP],
    key=lambda x: x[0]
)
_TRED_R2_ARR = np.array([e[0] for e in _tred_entries])
print(f"  T-redes listas ({len(_tred_entries)} entradas, {time.time()-_t0:.1f}s)", flush=True)

def mejor_tred(R2_target):
    idx0 = int(np.searchsorted(_TRED_R2_ARR, R2_target))
    lo=max(0,idx0-300); hi=min(len(_tred_entries),idx0+300)
    best = min(_tred_entries[lo:hi], key=lambda e: abs(e[0]-R2_target), default=None)
    if best is None: return None, None
    return best[0], (best[1], best[2], best[3])

# ════════════════════════════════════════════════════════════════════════════
# C2 EXTENDIDO: individual + paralelo + serie
# ════════════════════════════════════════════════════════════════════════════
def _build_c2_ext(caps):
    seen,res = {},[]
    for c in caps:
        if c not in seen: seen[c]=True; res.append((c,'S',None,None))
    for ca in caps:
        for cb in caps:
            if ca<=cb:
                cp=ca+cb
                if cp not in seen: seen[cp]=True; res.append((cp,'P',ca,cb))
                cs=ca*cb/(ca+cb)
                if cs not in seen: seen[cs]=True; res.append((cs,'E',ca,cb))
    return res

print("  Precomputando C2 ext. (paralelo+serie)...", flush=True)
_t0 = time.time()
_C2_EXT = _build_c2_ext(CAP_COMERCIALES)
print(f"  C2 ext. listo: {len(_C2_EXT)} valores ({time.time()-_t0:.1f}s)", flush=True)

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES TF (numpy)
# ════════════════════════════════════════════════════════════════════════════
def H_bp_real(R1, R2, C1, C2, freqs):
    s      = 1j*2*np.pi*freqs
    beta   = (s*R2*C1)/((s*R1*C1+1)*(s*R2*C2+1))
    z_load = (R2/Rin_bp_amp)/(s*R2*C2+1)
    nif    = (1+s/omega_p_bp)/A0_bp
    return -beta/(1+nif*(1+beta+z_load))

def circuito_params(R1, R2, C1, C2):
    R2e=R2*Rin_bp_amp/(R2+Rin_bp_amp); t1=R1*C1; t2=R2e*C2
    if t1<=0 or t2<=0: return None,None
    return 1/(2*np.pi*np.sqrt(t1*t2)), (t1+t2)/(2*np.sqrt(t1*t2))

_S_EVAL      = 1j*2*np.pi*FREQS_EVAL
_NIF_ADD     = (1+_S_EVAL/omega_p_add)/A0_add
_OMEGA_EVAL  = 2*np.pi*FREQS_EVAL
_NF          = len(FREQS_EVAL)

# ════════════════════════════════════════════════════════════════════════════
# FASE 1: Generar candidatos (numpy vectorizado)
# ════════════════════════════════════════════════════════════════════════════
def fase1_vectorizada():
    R1_np = np.array(_RES_BP, dtype=np.float64)
    C1_np = CAP_COMERCIALES  # todos los caps como C1
    C2_np = np.array([e[0] for e in _C2_EXT], dtype=np.float64)
    C2_types = [(e[1],e[2],e[3]) for e in _C2_EXT]
    n1,nc1,nc2 = len(R1_np), len(C1_np), len(C2_np)
    print(f"  Fase 1: {n1}×{nc1}×{nc2} = {n1*nc1*nc2:,} triples...", flush=True)
    _t = time.time()
    R1m = R1_np.reshape(n1,1,1)
    C1m = np.array(C1_np, dtype=np.float64).reshape(1,nc1,1)
    C2m = C2_np.reshape(1,1,nc2)
    tau1    = R1m*C1m
    R2_ideal = 1.0/(omega0**2 * tau1 * C2m)
    valid   = (R2_ideal >= R_min_bp) & (R2_ideal <= R2_max_tred)
    idxs    = np.argwhere(valid)
    print(f"  Fase 1 completa: {len(idxs):,} candidatos con R2 en rango ({time.time()-_t:.2f}s)", flush=True)
    cands = []
    for i,j,l in idxs:
        typ,ca,cb = C2_types[l]
        cands.append({'R1':float(R1_np[i]), 'C1':float(C1_np[j]),
                      'C2':float(C2_np[l]), 'C2_type':typ, 'C2_a':ca, 'C2_b':cb,
                      'R2_ideal':float(R2_ideal[i,j,l])})
    return cands

# ════════════════════════════════════════════════════════════════════════════
# FASE 2: Cuantizar R2 + calcular ζ (vectorizado numpy)
# ════════════════════════════════════════════════════════════════════════════
def fase2_cuantizar(cands):
    R1_a  = np.array([c['R1'] for c in cands])
    C1_a  = np.array([c['C1'] for c in cands])
    C2_a  = np.array([c['C2'] for c in cands])
    R2i_a = np.array([c['R2_ideal'] for c in cands])
    R2c_a = res_comercial_batch(R2i_a)                     # vectorizado
    R2e_a = R2c_a*Rin_bp_amp/(R2c_a+Rin_bp_amp)
    tau1  = R1_a*C1_a; tau2 = R2e_a*C2_a
    with np.errstate(invalid='ignore', divide='ignore'):
        f0_a   = 1/(2*np.pi*np.sqrt(tau1*tau2))
        zeta_a = (tau1+tau2)/(2*np.sqrt(tau1*tau2))
    # Filtro f0 (después de cuantizar R2)
    f0err_a = np.abs(f0_a-f0_des)/f0_des*100
    valid   = np.isfinite(f0_a) & np.isfinite(zeta_a) & (f0err_a <= F0_TOL_PCT)
    # Deduplicar por (R1,C1,C2,R2c)
    keys, keep = set(), []
    for i in np.where(valid)[0]:
        k = (R1_a[i], C1_a[i], C2_a[i], round(float(R2c_a[i]),1))
        if k not in keys:
            keys.add(k)
            c = cands[i].copy()
            c.update(R2_act=float(R2c_a[i]), zeta=float(zeta_a[i]),
                     f0=float(f0_a[i]), f0_err=float(f0err_a[i]))
            keep.append(c)
    return keep

# ════════════════════════════════════════════════════════════════════════════
# FASE 3: GPU batch — H_bp, H_ideal_ref(ζ_cand), lstsq, H_total, error banda
# ════════════════════════════════════════════════════════════════════════════

def _passband_error_numpy(H_bp_n, zeta_n):
    """
    Para N candidatos (numpy). Devuelve (err_banda, K_opt_dB, a_opt, b_opt).
    H_bp_n: (N, F) complex128
    zeta_n: (N,) float64
    """
    N = len(zeta_n)
    # H_ideal_ref per candidate: (N, F)
    s = _S_EVAL[None,:]                                   # (1, F)
    numer = s**2 + 2*ZETA_GEO*omega0*s + omega0**2        # (1, F)
    denom = s**2 + 2*zeta_n[:,None]*omega0*s + omega0**2  # (N, F)
    H_ref = numer / denom                                 # (N, F)

    # Batch lstsq via normal equations
    c1  = H_bp_n.sum(axis=1)                              # (N,) complex
    c2  = (np.abs(H_bp_n)**2).sum(axis=1)                 # (N,) real
    d1  = H_ref.sum(axis=1)                               # (N,) complex
    d2  = (np.conj(H_bp_n)*H_ref).sum(axis=1)             # (N,) complex
    det = _NF*c2 - np.abs(c1)**2                          # (N,) real
    det = np.where(np.abs(det)<1e-30, 1e-30, det)
    a   = ((c2*d1 - np.conj(c1)*d2)/det).real             # (N,)
    b   = ((_NF*d2 - c1*d1)/det).real                     # (N,)

    # H_total continuo
    H_tot = -(a[:,None] + b[:,None]*H_bp_n)               # (N, F)

    # Passband mask per candidate
    sqrt_t  = np.sqrt(np.maximum(zeta_n**2-1, 0))
    om_lo   = omega0*(zeta_n - sqrt_t)                    # (N,)
    om_hi   = omega0*(zeta_n + sqrt_t)                    # (N,)
    om      = _OMEGA_EVAL[None,:]                          # (1, F)
    mask_pb = (om >= om_lo[:,None]) & (om <= om_hi[:,None]) # (N, F)
    # fallback: si ninguna freq en banda, usar todo el rango
    no_band = mask_pb.sum(axis=1) == 0
    mask_pb[no_band] = True

    # Error en banda
    mag_t = np.abs(H_tot)
    mag_r = np.abs(H_ref)
    ok    = (mag_t>1e-30) & (mag_r>1e-30) & mask_pb
    with np.errstate(divide='ignore', invalid='ignore'):
        log_t = np.where(mag_t>1e-30, 20*np.log10(mag_t), np.nan)
        log_r = np.where(mag_r>1e-30, 20*np.log10(mag_r), np.nan)
    diff   = np.where(ok, log_r - log_t, np.nan)
    K_dB   = np.nanmean(diff, axis=1)
    err    = np.sqrt(np.nanmean((diff - K_dB[:,None])**2, axis=1))
    return err, K_dB, a, b

def _passband_error_gpu(H_bp_t, zeta_t):
    """Igual que numpy pero en CUDA. Devuelve tensores en CPU."""
    N = zeta_t.shape[0]
    s = torch.tensor(_S_EVAL, dtype=torch.complex64, device=device).view(1,-1)
    numer = s**2 + 2*ZETA_GEO*omega0*s + omega0**2
    denom = s**2 + 2*zeta_t.view(N,1).to(torch.complex64)*omega0*s + omega0**2
    H_ref = numer / denom                                 # (N, F)

    c1  = H_bp_t.sum(dim=1)
    c2  = H_bp_t.abs().pow(2).sum(dim=1).real
    d1  = H_ref.sum(dim=1)
    d2  = (H_bp_t.conj()*H_ref).sum(dim=1)
    det = _NF*c2 - c1.abs().pow(2)
    det = torch.where(det.abs()<1e-30, torch.tensor(1e-30,device=device), det)
    a   = ((c2*d1 - c1.conj()*d2)/det).real
    b   = ((_NF*d2 - c1*d1)/det).real

    H_tot = -(a.view(N,1) + b.view(N,1)*H_bp_t)

    sqrt_t  = torch.sqrt((zeta_t**2 - 1).clamp(min=0))
    om_lo   = omega0*(zeta_t - sqrt_t)
    om_hi   = omega0*(zeta_t + sqrt_t)
    om      = torch.tensor(_OMEGA_EVAL, dtype=torch.float32, device=device).view(1,-1)
    mask_pb = (om >= om_lo.view(N,1)) & (om <= om_hi.view(N,1))
    no_band = mask_pb.sum(dim=1) == 0
    mask_pb[no_band] = True

    mag_t = H_tot.abs(); mag_r = H_ref.abs()
    ok    = (mag_t>1e-30) & (mag_r>1e-30) & mask_pb
    with torch.no_grad():
        log_t = torch.where(mag_t>1e-30, 20*torch.log10(mag_t), torch.tensor(float('nan'),device=device))
        log_r = torch.where(mag_r>1e-30, 20*torch.log10(mag_r), torch.tensor(float('nan'),device=device))
    diff  = torch.where(ok, log_r-log_t, torch.tensor(float('nan'),device=device))
    K_dB  = diff.nanmean(dim=1)
    err   = ((diff - K_dB.view(N,1)).pow(2)).nanmean(dim=1).sqrt()
    return err.cpu().numpy(), K_dB.cpu().numpy(), a.cpu().numpy(), b.cpu().numpy()

def fase3_evaluar_banda(cands):
    """Devuelve solo los candidatos con error de banda < MAX_ERR_BANDA_dB.
    H_bp se calcula directamente en GPU (evita tensor numpy de varios GB).
    """
    R1_a  = np.array([c['R1']     for c in cands], dtype=np.float32)
    R2_a  = np.array([c['R2_act'] for c in cands], dtype=np.float32)
    C1_a  = np.array([c['C1']     for c in cands], dtype=np.float32)
    C2_a  = np.array([c['C2']     for c in cands], dtype=np.float32)
    Z_a   = np.array([c['zeta']   for c in cands], dtype=np.float32)

    N = len(cands)
    err_all = np.full(N, np.inf, dtype=np.float32)
    Kdg_all = np.zeros(N, dtype=np.float32)
    a_all   = np.zeros(N, dtype=np.float32)
    b_all   = np.zeros(N, dtype=np.float32)

    use_gpu = (torch is not None and device is not None and BACKEND == 'cuda')

    # Constantes BP en GPU (escalares, float32)
    _RP  = float(omega_p_bp); _A0 = float(A0_bp); _RIN = float(Rin_bp_amp)
    _s_t = torch.tensor(_S_EVAL.astype(np.complex64), device=device) if use_gpu else None

    for lo in range(0, N, GPU_CHUNK):
        hi = min(lo + GPU_CHUNK, N)
        sl = slice(lo, hi)
        Nc = hi - lo
        zc = Z_a[sl]

        if use_gpu:
            try:
                # ── Parámetros en GPU (float32 → complex64 para aritmética) ──────
                R1t = torch.tensor(R1_a[sl], device=device).view(Nc, 1).to(torch.complex64)
                R2t = torch.tensor(R2_a[sl], device=device).view(Nc, 1).to(torch.complex64)
                C1t = torch.tensor(C1_a[sl], device=device).view(Nc, 1).to(torch.complex64)
                C2t = torch.tensor(C2_a[sl], device=device).view(Nc, 1).to(torch.complex64)
                zt  = torch.tensor(zc, device=device)        # (Nc,) float32
                ss  = _s_t.view(1, _NF)                      # (1, F) complex64

                # ── H_bp en GPU ────────────────────────────────────────────────
                beta   = (ss*R2t*C1t) / ((ss*R1t*C1t + 1) * (ss*R2t*C2t + 1))
                zload  = (R2t / _RIN) / (ss*R2t*C2t + 1)
                nif_bp = (1 + ss / _RP) / _A0
                H_bp_t = -beta / (1 + nif_bp*(1 + beta + zload))  # (Nc, F)

                e, k, a, b = _passband_error_gpu(H_bp_t, zt)
                torch.cuda.empty_cache()
            except RuntimeError as ex:
                torch.cuda.empty_cache()
                # Fallback numpy si OOM
                r1c = R1_a[sl].astype(np.float64)[:, None]
                r2c = R2_a[sl].astype(np.float64)[:, None]
                c1c = C1_a[sl].astype(np.float64)[:, None]
                c2c = C2_a[sl].astype(np.float64)[:, None]
                ss_n = _S_EVAL.reshape(1, _NF)
                beta_n  = (ss_n*r2c*c1c)/((ss_n*r1c*c1c+1)*(ss_n*r2c*c2c+1))
                zld_n   = (r2c/Rin_bp_amp)/(ss_n*r2c*c2c+1)
                nif_n   = (1+ss_n/omega_p_bp)/A0_bp
                H_bp_c  = -beta_n/(1+nif_n*(1+beta_n+zld_n))
                e, k, a, b = _passband_error_numpy(H_bp_c, zc.astype(np.float64))
        else:
            r1c = R1_a[sl].astype(np.float64)[:, None]
            r2c = R2_a[sl].astype(np.float64)[:, None]
            c1c = C1_a[sl].astype(np.float64)[:, None]
            c2c = C2_a[sl].astype(np.float64)[:, None]
            ss_n = _S_EVAL.reshape(1, _NF)
            beta_n  = (ss_n*r2c*c1c)/((ss_n*r1c*c1c+1)*(ss_n*r2c*c2c+1))
            zld_n   = (r2c/Rin_bp_amp)/(ss_n*r2c*c2c+1)
            nif_n   = (1+ss_n/omega_p_bp)/A0_bp
            H_bp_c  = -beta_n/(1+nif_n*(1+beta_n+zld_n))
            e, k, a, b = _passband_error_numpy(H_bp_c, zc.astype(np.float64))

        err_all[sl] = e; Kdg_all[sl] = k
        a_all[sl]   = a; b_all[sl]   = b

    passing = np.where(np.isfinite(err_all) & (err_all <= MAX_ERR_BANDA_dB))[0]
    result  = []
    for i in passing:
        c = cands[i].copy()
        c.update(err_banda=float(err_all[i]), K_opt_dB=float(Kdg_all[i]),
                 a_opt=float(a_all[i]), b_opt=float(b_all[i]))
        result.append(c)
    return result

# ════════════════════════════════════════════════════════════════════════════
# FASE 4: T-net R2 + cuantización adder (CPU paralelo)
# ════════════════════════════════════════════════════════════════════════════

def _proc_chunk(cands_chunk):
    """Proceso worker: T-net R2 + adder comercial para un chunk."""
    results = []
    for c in cands_chunk:
        R1=c['R1']; C1=c['C1']; C2=c['C2']
        R2_i=c['R2_ideal']; R2_c=c['R2_act']

        # T-net vs single
        err_s = abs(R2_c-R2_i)/R2_i*100 if R2_i > 0 else np.inf
        R2_tnet, tdata = mejor_tred(R2_i)
        err_t = abs(R2_tnet-R2_i)/R2_i*100 if R2_tnet else np.inf
        if R2_tnet and err_t < err_s:
            R2_act=R2_tnet; r2_type='T'; r2_err=err_t
            Ra,Rb,Rc = tdata
        else:
            R2_act=R2_c; r2_type='S'; r2_err=err_s
            Ra=Rb=Rc=None

        a_opt=c['a_opt']; b_opt=c['b_opt']
        if a_opt <= 0 or b_opt <= 0:
            continue

        # Mejor Rf comercial
        Ru_ids  = _RES_ADD_ARR/a_opt
        Rbp_ids = _RES_ADD_ARR/b_opt
        mask    = ((Ru_ids>=R_min_add)&(Ru_ids<=R_max_add)&
                   (Rbp_ids>=R_min_add)&(Rbp_ids<=R_max_add))
        Rf_v = _RES_ADD_ARR[mask]
        if len(Rf_v)==0: continue
        Ru_v  = res_comercial_batch(Rf_v/a_opt)
        Rbp_v = res_comercial_batch(Rf_v/b_opt)
        a_v   = (Rf_v/Ru_v)[:,None];  b_v=(Rf_v/Rbp_v)[:,None]
        Rf_2d = Rf_v[:,None]
        # Av superposition (ideal para ranking)
        Av_u  = -a_v/(1+_NIF_ADD[None,:]*(1+a_v+Rf_2d/Rin_add))
        Av_bp_n=-b_v/(1+_NIF_ADD[None,:]*(1+b_v+Rf_2d/Rin_add))
        # H_bp con R2_act
        H_bp = H_bp_real(R1, R2_act, C1, C2, FREQS_EVAL)
        H_tot = Av_u + Av_bp_n*H_bp[None,:]
        mag_t = np.abs(H_tot)
        # H_ideal_ref con zeta del candidato
        s_e = _S_EVAL
        zc  = c['zeta']
        H_ref = (s_e**2+2*ZETA_GEO*omega0*s_e+omega0**2)/(s_e**2+2*zc*omega0*s_e+omega0**2)
        mag_r = np.abs(H_ref)
        # Passband mask
        st   = np.sqrt(max(zc**2-1, 0))
        oml  = omega0*(zc-st); omh = omega0*(zc+st)
        pb   = (_OMEGA_EVAL>=oml) & (_OMEGA_EVAL<=omh)
        if not pb.any(): pb = np.ones(_NF, dtype=bool)
        with np.errstate(divide='ignore', invalid='ignore'):
            lt = np.where(mag_t>1e-30, 20*np.log10(mag_t), np.nan)
            lr = np.where(mag_r>1e-30, 20*np.log10(mag_r[None,:]), np.nan)
        diff   = np.where((mag_t>1e-30)&(mag_r>1e-30)&pb[None,:], lr-lt, np.nan)
        K_dBv  = np.nanmean(diff, axis=1)
        err_v  = np.sqrt(np.nanmean((diff-K_dBv[:,None])**2, axis=1))
        best_i = int(np.nanargmin(err_v))
        if not np.isfinite(err_v[best_i]): continue

        out = c.copy()
        out.update(
            R2_act=R2_act, r2_type=r2_type, r2_err=r2_err,
            T_Ra=Ra, T_Rb=Rb, T_Rc=Rc,
            Ru=float(Ru_v[best_i]), Rbp=float(Rbp_v[best_i]),
            Rf=float(Rf_v[best_i]),
            Rf_Ru=float(Rf_v[best_i]/Ru_v[best_i]),
            Rf_Rbp=float(Rf_v[best_i]/Rbp_v[best_i]),
            err_banda_final=float(err_v[best_i]),
            K_opt_dB_final=float(K_dBv[best_i]),
        )
        results.append(out)
    return results

def fase4_paralela(cands):
    chunks  = [cands[i:i+CPU_CHUNK] for i in range(0, len(cands), CPU_CHUNK)]
    results = []
    with ProcessPoolExecutor(max_workers=N_CPU_WORKERS) as ex:
        futs = {ex.submit(_proc_chunk, ch): ch for ch in chunks}
        with tqdm(total=len(cands), desc="Fase 4 adder+T-net", unit="cand", ncols=90) as pb:
            for fut in as_completed(futs):
                res = fut.result()
                results.extend(res)
                pb.update(len(futs[fut]))
                pb.set_postfix_str(f"pasando={len(results)}")
    return results

# ════════════════════════════════════════════════════════════════════════════
# ESCRITURA CSV (estructura unificada, orden ζ)
# ════════════════════════════════════════════════════════════════════════════
HEADER = [
    "zeta","rank","err_banda_dB","K_opt_dB","f0_Hz","f0_err_pct",
    "C1_F","C2_F","C2_type","C2_a_F","C2_b_F",
    "R1_ohm","R2_ideal_ohm","R2_act_ohm","R2_type","R2_err_pct",
    "T_Ra","T_Rb","T_Rc",
    "Ru_ohm","Rbp_ohm","Rf_ohm","Rf_Ru","Rf_Rbp",
]

def escribir_csv(results, path):
    results.sort(key=lambda x: x.get('zeta',0))
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for i, r in enumerate(results, 1):
            w.writerow([
                round(r.get('zeta',0),4),
                i,
                round(r.get('err_banda_final', r.get('err_banda',0)),6),
                round(r.get('K_opt_dB_final', r.get('K_opt_dB',0)),4),
                r.get('f0',0), round(r.get('f0_err',0),4),
                r.get('C1'), r.get('C2'), r.get('C2_type'),
                r.get('C2_a') or '', r.get('C2_b') or '',
                r.get('R1'), r.get('R2_ideal'), r.get('R2_act'),
                r.get('r2_type',''), round(r.get('r2_err',0),4),
                r.get('T_Ra',''), r.get('T_Rb',''), r.get('T_Rc',''),
                r.get('Ru'), r.get('Rbp'), r.get('Rf'),
                round(r.get('Rf_Ru',0),6), round(r.get('Rf_Rbp',0),6),
            ])
    print(f"  CSV guardado: {path}  ({len(results)} filas)", flush=True)

# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    script_dir = os.path.dirname(os.path.abspath(__file__))
    t_total    = time.time()

    print(f"\n  Backend: {BACKEND}  |  CPUs: {N_CPU_WORKERS}"
          f"  |  MAX_ERR_BANDA: {MAX_ERR_BANDA_dB} dB"
          f"  |  GPU_CHUNK: {GPU_CHUNK:,}", flush=True)

    # Fase 1
    cands1 = fase1_vectorizada()

    # Fase 2: cuantizar R2, calcular ζ
    print(f"\n  Fase 2: cuantización R2 vectorizada...", flush=True)
    cands2 = fase2_cuantizar(cands1)
    print(f"  Fase 2 completa: {len(cands2):,} candidatos únicos con f0 ok", flush=True)
    del cands1  # liberar RAM

    # Fase 3: error de banda en GPU (chunks)
    print(f"\n  Fase 3: evaluación banda de paso en {BACKEND} (chunks de {GPU_CHUNK:,})...", flush=True)
    t3 = time.time()
    cands3 = []
    chunks3 = [cands2[i:i+GPU_CHUNK] for i in range(0, len(cands2), GPU_CHUNK)]
    with tqdm(total=len(cands2), desc="Fase 3 GPU", unit="cand", ncols=90) as pb:
        for ch in chunks3:
            pasando = fase3_evaluar_banda(ch)
            cands3.extend(pasando)
            pb.update(len(ch))
            pb.set_postfix_str(f"pasan={len(cands3)}")
    print(f"  Fase 3 completa: {len(cands3):,} pasan el filtro ({time.time()-t3:.1f}s)", flush=True)
    del cands2

    if not cands3:
        print(f"  Sin candidatos — probá aumentar MAX_ERR_BANDA_dB (actual={MAX_ERR_BANDA_dB})")
        raise SystemExit(0)

    # Fase 4: T-net R2 + adder comercial (CPU paralelo)
    print(f"\n  Fase 4: T-net R2 + adder comercial ({N_CPU_WORKERS} workers)...", flush=True)
    t4 = time.time()
    cands4 = fase4_paralela(cands3)
    print(f"  Fase 4 completa: {len(cands4):,} candidatos finales ({time.time()-t4:.1f}s)", flush=True)

    # Guardar
    csv_path = os.path.join(script_dir, "compensador_full.csv")
    escribir_csv(cands4, csv_path)
    print(f"\n  Total: {time.time()-t_total:.1f}s  —  {len(cands4):,} candidatos guardados")
