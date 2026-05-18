# -*- coding: utf-8 -*-
"""
calculoCompensadorGPU.py
Versión acelerada por GPU del compensador geófono.

Compatible con Intel Arc (via torch-directml), NVIDIA (CUDA) o CPU fallback.
Instalación:   pip install torch torch-directml

Speedup vs Simple:
  Phase 1: tensor (n_R1, n_C1, n_C2_ext) evaluado en un solo op GPU en lugar de
           bucle Python  ~20-100×
  Phase 2: H_bp y lstsq para todos los candidatos en batch GPU
           ~10-50×

VRAM: < 1 GB para este problema (cabe cómodamente en los 4 GB dedicados).
"""
import sys, os, time, csv
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import warnings
warnings.filterwarnings('ignore')

# ── GPU Detection ─────────────────────────────────────────────────────────────
BACKEND = 'cpu'
device  = None

try:
    import torch
    # Orden de prioridad: CUDA (NVIDIA) → XPU (Intel Arc) → DirectML → CPU
    if torch.cuda.is_available():
        device  = torch.device('cuda')
        BACKEND = 'cuda'
        _gpu_name = torch.cuda.get_device_name(0)
        _gpu_vram = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"  GPU detectada: {_gpu_name}  ({_gpu_vram:.1f} GB VRAM)", flush=True)
    elif hasattr(torch, 'xpu') and torch.xpu.is_available():
        device  = torch.device('xpu')
        BACKEND = 'xpu'
    else:
        try:
            import torch_directml
            device  = torch_directml.device()
            BACKEND = 'directml'
        except ImportError:
            device  = torch.device('cpu')
            BACKEND = 'cpu-torch'
except ImportError:
    torch = None
    print("  torch no instalado. Instalar: pip install torch", flush=True)

# Test si la GPU soporta complex64
_GPU_COMPLEX = False
if torch is not None and device is not None:
    try:
        _t = torch.ones(4, dtype=torch.complex64, device=device)
        _  = _t * _t
        _GPU_COMPLEX = True
    except Exception:
        pass

print(f"  Backend: {BACKEND}  |  complex64 GPU: {_GPU_COMPLEX}", flush=True)
if torch is None:
    print("  Para activar GPU NVIDIA 3050 instalar:\n"
          "  pip install torch --index-url https://download.pytorch.org/whl/cu121",
          flush=True)

try:
    from tqdm import tqdm
except ImportError:
    class tqdm:
        def __init__(self, it=None, total=None, desc='', unit='it', ncols=None, **kw):
            self._it=list(it) if it is not None else []; self._tot=total or len(self._it)
            self._n=0; self._t0=time.time(); self._desc=desc; self._last=-1
        def __iter__(self):
            for item in self._it:
                yield item; self._n+=1
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

import csv

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ════════════════════════════════════════════════════════════════════════════

# ── Geófono ──────────────────────────────────────────────────────────────────
f0_des      = 10
ZETA_GEO    = 0.25
ZETA_TARGET = 1000
omega0      = 2*np.pi*f0_des

# ── Op-amp BP ─────────────────────────────────────────────────────────────────
A0_dB_bp    = 90
A0_bp       = 10**(A0_dB_bp/20.0)
f_p_bp      = 8e6
omega_p_bp  = 2*np.pi*f_p_bp
Rin_bp_amp  = 35e6

# ── Op-amp Adder ──────────────────────────────────────────────────────────────
A0_dB_add   = 90
A0_add      = 10**(A0_dB_add/20.0)
f_p_add     = 8e6
omega_p_add = 2*np.pi*f_p_add
Rin_add     = 35e6

# ── Rangos ────────────────────────────────────────────────────────────────────
R_min_bp    = 1e3
R_max_bp    = 1e6
R_min_add   = 1e3
R_max_add   = 1e6
F0_TOL_PCT  = 10.0
ZETA_MIN    = 100
ZETA_MAX    = 1000
TOP_N       = 15

# ── GPU / partición de VRAM ───────────────────────────────────────────────────
# NVIDIA 3050: 4 GB VRAM.  (N × 800 × 8 bytes) < 3 GB → chunk de 400k candidatos
VRAM_DEDICATED_GB = 4.0
VRAM_PHASE2_CHUNK = 400_000  # candidatos Phase-2 por chunk GPU

# ── Frecuencias ───────────────────────────────────────────────────────────────
FREQS_EVAL = np.logspace(np.log10(f0_des/1000), np.log10(1e5), 800)

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

CAPS_TODOS     = list(CAP_COMERCIALES)
CAPS_CERAMICOS = [c for c in CAP_COMERCIALES if c <= 100e-9]

_RES_BP      = [r for r in RES_COMERCIALES if R_min_bp  <= r <= R_max_bp]
_RES_ADD     = [r for r in RES_COMERCIALES if R_min_add <= r <= R_max_add]
_RES_ADD_ARR = np.array(_RES_ADD, dtype=np.float64)
_RES_ARR_ALL = np.array(RES_COMERCIALES, dtype=np.float64)
_CAP_ARR     = np.array(CAP_COMERCIALES, dtype=np.float64)

def res_comercial_batch(vals):
    """Versión vectorizada de res_comercial para arrays numpy."""
    vals = np.asarray(vals, dtype=np.float64)
    idx  = np.searchsorted(_RES_ARR_ALL, vals, side='left')
    idx  = np.clip(idx, 0, len(_RES_ARR_ALL)-1)
    idx_l = np.maximum(idx-1, 0)
    use_left = np.abs(_RES_ARR_ALL[idx_l]-vals) < np.abs(_RES_ARR_ALL[idx]-vals)
    return _RES_ARR_ALL[np.where(use_left, idx_l, idx)]

def fmt_cap(c):
    if c >= 1e-6: return f"{c*1e6:.3g}uF"
    if c >= 1e-9: return f"{c*1e9:.3g}nF"
    return f"{c*1e12:.3g}pF"

def fmt_res(r):
    if r >= 1e6: return f"{r/1e6:.3g}Mohm"
    if r >= 1e3: return f"{r/1e3:.3g}kohm"
    return f"{r:.3g}ohm"

def res_comercial(v):
    return float(RES_COMERCIALES[int(np.argmin(np.abs(_RES_ARR_ALL - v)))])

def cap_comercial(v):
    return float(CAP_COMERCIALES[int(np.argmin(np.abs(_CAP_ARR - v)))])

# ════════════════════════════════════════════════════════════════════════════
# T-REDES R2 (CPU)
# ════════════════════════════════════════════════════════════════════════════
_Ra_max = max(_RES_BP); _Rc_min = min(_RES_BP)
R2_max_tred = 2*_Ra_max + _Ra_max**2/_Rc_min

print(f"  Precomputando T-redes R2...", flush=True)
_t0 = time.time()
_tred_entries = sorted(
    [(_Ra+_Rb+_Ra*_Rb/_Rc, _Ra, _Rb, _Rc)
     for _Ra in _RES_BP for _Rb in _RES_BP for _Rc in _RES_BP],
    key=lambda x: x[0]
)
_TRED_R2_ARR = np.array([e[0] for e in _tred_entries])
print(f"  T-redes listas ({len(_tred_entries)} entradas, {time.time()-_t0:.1f}s)", flush=True)

def mejores_tred(R2_target, top_n=5):
    idx0 = int(np.searchsorted(_TRED_R2_ARR, R2_target))
    lo=max(0,idx0-300); hi=min(len(_tred_entries),idx0+300)
    seen,out = set(),[]
    for R2_eq,Ra,Rb,Rc in sorted(_tred_entries[lo:hi], key=lambda e: abs(e[0]-R2_target)):
        norm=(min(Ra,Rb),max(Ra,Rb),Rc)
        if norm not in seen:
            seen.add(norm)
            out.append(dict(Ra=Ra,Rb=Rb,Rc=Rc,R2_eq=R2_eq,
                            err=abs(R2_eq-R2_target)/R2_target*100))
        if len(out)>=top_n: break
    return out

# ════════════════════════════════════════════════════════════════════════════
# C2 EXTENDIDO: individual + paralelo + serie (CPU)
# ════════════════════════════════════════════════════════════════════════════
def _build_c2_ext(caps_list):
    seen,result = {},[]
    for c in caps_list:
        if c not in seen: seen[c]=True; result.append((c,'S',None,None))
    for ca in caps_list:
        for cb in caps_list:
            if ca<=cb:
                cp=ca+cb
                if cp not in seen: seen[cp]=True; result.append((cp,'P',ca,cb))
                cs=ca*cb/(ca+cb)
                if cs not in seen: seen[cs]=True; result.append((cs,'E',ca,cb))
    return result

print("  Precomputando C2 ext. (paralelo+serie)...", flush=True)
_t0 = time.time()
_C2_EXT_TODOS = _build_c2_ext(CAPS_TODOS)
_C2_EXT_CERAM = _build_c2_ext(CAPS_CERAMICOS)
print(f"  C2 ext. listo: {len(_C2_EXT_TODOS)} TODOS / "
      f"{len(_C2_EXT_CERAM)} CERÁMICOS  ({time.time()-_t0:.1f}s)", flush=True)

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES TF (numpy, para CPU fallback y output)
# ════════════════════════════════════════════════════════════════════════════

def H_real_arr(R1, R2, C1, C2, freqs_hz):
    s      = 1j*2*np.pi*freqs_hz
    beta   = (s*R2*C1)/((s*R1*C1+1)*(s*R2*C2+1))
    z_load = (R2/Rin_bp_amp)/(s*R2*C2+1)
    nif    = (1+s/omega_p_bp)/A0_bp
    return -beta/(1+nif*(1+beta+z_load))

def H_ideal_arr(freqs_hz):
    s = 1j*2*np.pi*freqs_hz
    return (s**2+2*ZETA_GEO*omega0*s+omega0**2)/(s**2+2*ZETA_TARGET*omega0*s+omega0**2)

def circuito_params(R1, R2, C1, C2):
    R2_eff=R2*Rin_bp_amp/(R2+Rin_bp_amp)
    tau1=R1*C1; tau2=R2_eff*C2
    if tau1<=0 or tau2<=0: return None,None
    return 1/(2*np.pi*np.sqrt(tau1*tau2)), (tau1+tau2)/(2*np.sqrt(tau1*tau2))

_H_IDEAL_EVAL  = H_ideal_arr(FREQS_EVAL)
_S_EVAL        = 1j*2*np.pi*FREQS_EVAL
_NIF_ADD_EVAL  = (1+_S_EVAL/omega_p_add)/A0_add
_LOG_MAG_ID    = 20*np.log10(np.abs(_H_IDEAL_EVAL))

# ════════════════════════════════════════════════════════════════════════════
# GPU BATCH: Phase 1
# Evalúa TODOS los triples (R1, C1, C2_ext) en un solo op GPU.
# ════════════════════════════════════════════════════════════════════════════

def phase1_gpu(caps_search, c2_ext, label):
    """
    Devuelve lista de dicts con candidatos Phase-1 válidos.
    Si GPU disponible y complex64 soportado: usa tensores GPU.
    Si no: numpy vectorizado (sin bucle Python).
    """
    R1_np  = np.array(_RES_BP, dtype=np.float32)
    C1_np  = np.array(caps_search, dtype=np.float32)
    C2_np  = np.array([e[0] for e in c2_ext], dtype=np.float32)
    C2_types = [(e[1], e[2], e[3]) for e in c2_ext]

    n1, nc1, nc2 = len(R1_np), len(C1_np), len(C2_np)
    print(f"  [{label}] Phase 1 GPU: {n1}×{nc1}×{nc2} = {n1*nc1*nc2:,} triples...",
          flush=True)
    _t = time.time()

    if torch is not None and device is not None and BACKEND != 'cpu-torch':
        # ── GPU path ──────────────────────────────────────────────────────
        R1t = torch.tensor(R1_np, device=device).view(n1,  1,  1)
        C1t = torch.tensor(C1_np, device=device).view(1,  nc1, 1)
        C2t = torch.tensor(C2_np, device=device).view(1,   1, nc2)

        tau1 = R1t * C1t                          # (n1, nc1, 1)
        tau2_tgt = 1.0 / (float(omega0)**2 * tau1 * C2t)  # (n1, nc1, nc2)

        valid_R2 = (tau2_tgt >= R_min_bp) & (tau2_tgt <= R2_max_tred)

        R2_eff = tau2_tgt * float(Rin_bp_amp) / (tau2_tgt + float(Rin_bp_amp))
        tau2   = R2_eff * C2t
        sqrt_t = torch.sqrt(tau1 * tau2 + 1e-60)
        zeta   = (tau1 + tau2) / (2 * sqrt_t)

        valid_z = (zeta >= ZETA_MIN) & (zeta <= ZETA_MAX)
        valid   = valid_R2 & valid_z

        idxs = valid.nonzero(as_tuple=False).cpu().numpy()  # (N_valid, 3)
        R2_ideals = tau2_tgt.cpu().numpy()
    else:
        # ── CPU numpy vectorized path ──────────────────────────────────────
        R1m = R1_np.reshape(n1, 1, 1)
        C1m = C1_np.reshape(1, nc1, 1)
        C2m = C2_np.reshape(1, 1, nc2)
        tau1     = R1m * C1m
        R2_ideal = 1.0 / (omega0**2 * tau1 * C2m)
        valid_R2 = (R2_ideal >= R_min_bp) & (R2_ideal <= R2_max_tred)
        R2_eff   = R2_ideal * Rin_bp_amp / (R2_ideal + Rin_bp_amp)
        tau2     = R2_eff * C2m
        zeta     = (tau1 + tau2) / (2*np.sqrt(tau1*tau2 + 1e-60))
        valid_z  = (zeta >= ZETA_MIN) & (zeta <= ZETA_MAX)
        valid    = valid_R2 & valid_z
        idxs     = np.argwhere(valid)
        R2_ideals = R2_ideal

    print(f"    {len(idxs):,} candidatos en {time.time()-_t:.2f}s", flush=True)

    phase1 = []
    for i, j, l in idxs:
        c2_type, c2_a, c2_b = C2_types[l]
        phase1.append(dict(
            R1=float(R1_np[i]), C1=float(C1_np[j]),
            C2=float(C2_np[l]), C2_type=c2_type, C2_a=c2_a, C2_b=c2_b,
            R2_ideal=float(R2_ideals[i,j,l])
        ))
    return phase1

# ════════════════════════════════════════════════════════════════════════════
# GPU BATCH: Phase 2 — H_bp + lstsq para N candidatos simultáneamente
# ════════════════════════════════════════════════════════════════════════════

def _H_bp_batch_gpu_complex(R1_t, R2_t, C1_t, C2_t, s_t):
    """H_bp para N candidatos. Requiere complex64 en GPU."""
    R1=R1_t.view(-1,1); R2=R2_t.view(-1,1)
    C1=C1_t.view(-1,1); C2=C2_t.view(-1,1)
    s =s_t.view(1,-1).to(torch.complex64)
    R1c=R1.to(torch.complex64); R2c=R2.to(torch.complex64)
    C1c=C1.to(torch.complex64); C2c=C2.to(torch.complex64)
    nif_const = torch.tensor(1/A0_bp, dtype=torch.complex64, device=R1_t.device)
    op_const  = torch.tensor(1/omega_p_bp/A0_bp, dtype=torch.complex64, device=R1_t.device)
    nif  = nif_const + s*op_const
    beta = (s*R2c*C1c)/((s*R1c*C1c+1)*(s*R2c*C2c+1))
    zl   = (R2c/Rin_bp_amp)/(s*R2c*C2c+1)
    return -beta/(1+nif*(1+beta+zl))   # (N, F) complex64

def _H_bp_batch_numpy(R1_np, R2_np, C1_np, C2_np):
    """H_bp en numpy (CPU), vectorizado sobre N candidatos."""
    N = len(R1_np); F = len(_S_EVAL)
    R1=R1_np.reshape(N,1); R2=R2_np.reshape(N,1)
    C1=C1_np.reshape(N,1); C2=C2_np.reshape(N,1)
    s=_S_EVAL.reshape(1,F)
    beta  = (s*R2*C1)/((s*R1*C1+1)*(s*R2*C2+1))
    zload = (R2/Rin_bp_amp)/(s*R2*C2+1)
    nif   = (1+s/omega_p_bp)/A0_bp
    return -beta/(1+nif*(1+beta+zload))   # (N, F) complex128

def _lstsq_batch(H_bp_np):
    """
    Batch lstsq: [1, H_bp] @ [a,b]^T ≈ H_ideal  (complex normal equations).
    H_bp_np: (N, F) complex numpy array
    Returns: a_opt (N,), b_opt (N,) float arrays
    """
    N, F = H_bp_np.shape
    c1 = H_bp_np.sum(axis=1)                           # (N,) complex
    c2 = (np.abs(H_bp_np)**2).sum(axis=1)              # (N,) real
    d1 = _H_IDEAL_EVAL.sum()                            # scalar complex
    d2 = (np.conj(H_bp_np) * _H_IDEAL_EVAL).sum(axis=1)  # (N,) complex
    det = F*c2 - np.abs(c1)**2                          # (N,) real  (> 0 ideally)
    det = np.where(np.abs(det) < 1e-30, 1e-30, det)
    a = ((c2*d1 - np.conj(c1)*d2) / det).real          # (N,) real
    b = ((F*d2 - c1*d1) / det).real                     # (N,) real
    return a, b

def phase2_batch_gpu(candidates, label):
    """
    Evalúa todos los candidatos Phase-2 en batch GPU o numpy vectorizado.
    Retorna: lista de dicts con H_bp, a_opt, b_opt añadidos.
    """
    N = len(candidates)
    if N == 0: return []
    print(f"  [{label}] Phase 2 GPU batch: {N:,} candidatos...", flush=True)
    _t = time.time()

    R1_np = np.array([c['R1'] for c in candidates], dtype=np.float32)
    R2_np = np.array([c['R2_act'] for c in candidates], dtype=np.float32)
    C1_np = np.array([c['C1'] for c in candidates], dtype=np.float32)
    C2_np = np.array([c['C2'] for c in candidates], dtype=np.float32)

    H_bp_all = np.empty((N, len(FREQS_EVAL)), dtype=np.complex64)
    a_all    = np.empty(N, dtype=np.float64)
    b_all    = np.empty(N, dtype=np.float64)

    # Procesar en chunks (VRAM_PHASE2_CHUNK candidatos por vez)
    for lo in range(0, N, VRAM_PHASE2_CHUNK):
        hi   = min(lo + VRAM_PHASE2_CHUNK, N)
        sl   = slice(lo, hi)
        r1c  = R1_np[sl]; r2c = R2_np[sl]
        c1c  = C1_np[sl]; c2c = C2_np[sl]

        if torch is not None and device is not None and _GPU_COMPLEX:
            try:
                R1t = torch.tensor(r1c, device=device)
                R2t = torch.tensor(r2c, device=device)
                C1t = torch.tensor(c1c, device=device)
                C2t = torch.tensor(c2c, device=device)
                s_t = torch.tensor(_S_EVAL.astype(np.complex64), device=device)
                H_chunk = _H_bp_batch_gpu_complex(R1t,R2t,C1t,C2t,s_t)
                H_np    = H_chunk.cpu().numpy()
            except Exception:
                H_np = _H_bp_batch_numpy(r1c.astype(np.float64), r2c.astype(np.float64),
                                         c1c.astype(np.float64), c2c.astype(np.float64))
        else:
            H_np = _H_bp_batch_numpy(r1c.astype(np.float64), r2c.astype(np.float64),
                                     c1c.astype(np.float64), c2c.astype(np.float64))

        H_np64 = H_np.astype(np.complex128)
        a_chunk, b_chunk = _lstsq_batch(H_np64)
        H_bp_all[sl] = H_np.astype(np.complex64)
        a_all[sl]    = a_chunk
        b_all[sl]    = b_chunk

    print(f"    H_bp+lstsq en {time.time()-_t:.2f}s", flush=True)
    return H_bp_all, a_all, b_all

# ════════════════════════════════════════════════════════════════════════════
# ADDER SEARCH (CPU vectorizado, usa H_bp y ratios precomputados)
# ════════════════════════════════════════════════════════════════════════════

def buscar_adder_fast(H_bp_np, a_opt, b_opt, top_n=5):
    """Busca (Ru,Rbp,Rf) comerciales dado H_bp y ratios óptimos precomputados."""
    if a_opt <= 0 or b_opt <= 0: return []
    Ru_id  = _RES_ADD_ARR / a_opt
    Rbp_id = _RES_ADD_ARR / b_opt
    mask   = ((Ru_id  >= R_min_add) & (Ru_id  <= R_max_add) &
              (Rbp_id >= R_min_add) & (Rbp_id <= R_max_add))
    Rf_v   = _RES_ADD_ARR[mask]
    if len(Rf_v) == 0: return []
    Ru_v  = np.array([res_comercial(v) for v in Rf_v/a_opt])
    Rbp_v = np.array([res_comercial(v) for v in Rf_v/b_opt])
    a_v   = (Rf_v/Ru_v)[:,None];  b_v = (Rf_v/Rbp_v)[:,None]
    Rf_2d = Rf_v[:,None];         nif = _NIF_ADD_EVAL[None,:]
    Av_u  = -a_v/(1+nif*(1+a_v+Rf_2d/Rin_add))
    Av_bp = -b_v/(1+nif*(1+b_v+Rf_2d/Rin_add))
    H_tot = Av_u + Av_bp*H_bp_np[None,:]
    mag_t = np.abs(H_tot)
    with np.errstate(divide='ignore', invalid='ignore'):
        log_t = np.where(mag_t>1e-30, 20*np.log10(mag_t), np.nan)
    diff    = _LOG_MAG_ID[None,:] - log_t
    K_dB    = np.nanmean(diff, axis=1)
    err_rms = np.sqrt(np.nanmean((diff-K_dB[:,None])**2, axis=1))
    seen,out = set(),[]
    for i in np.argsort(err_rms):
        key=(int(Ru_v[i]),int(Rbp_v[i]))
        if key in seen: continue
        seen.add(key)
        out.append(dict(Ru=float(Ru_v[i]),Rbp=float(Rbp_v[i]),Rf=float(Rf_v[i]),
                        Rf_type='S',a_real=float(Rf_v[i]/Ru_v[i]),
                        b_real=float(Rf_v[i]/Rbp_v[i]),
                        err_rms=float(err_rms[i]),K_opt_dB=float(K_dB[i])))
        if len(out)>=top_n: break
    return out

# ════════════════════════════════════════════════════════════════════════════
# BUCLE PRINCIPAL
# ════════════════════════════════════════════════════════════════════════════

def buscar_gpu(caps_search, c2_ext, label):

    # ── Phase 1 GPU ──────────────────────────────────────────────────────────
    phase1 = phase1_gpu(caps_search, c2_ext, label)

    # ── Phase 2 Pre-filter: vectorizado (sin loop Python) ───────────────────
    print(f"  [{label}] Pre-filtro vectorizado ({len(phase1):,} cands)...", flush=True)
    _t = time.time()

    # 1. Extraer arrays numpy de phase1 de una vez
    R1_all  = np.array([c['R1']       for c in phase1], dtype=np.float64)
    C1_all  = np.array([c['C1']       for c in phase1], dtype=np.float64)
    C2_all  = np.array([c['C2']       for c in phase1], dtype=np.float64)
    R2i_all = np.array([c['R2_ideal'] for c in phase1], dtype=np.float64)

    # 2. Cuantizar R2 a comercial (vectorizado, sin loop)
    R2c_all = res_comercial_batch(R2i_all)

    # 3. f0 y ζ con R2 comercial (vectorizado)
    R2eff_all = R2c_all * Rin_bp_amp / (R2c_all + Rin_bp_amp)
    tau1_all  = R1_all * C1_all
    tau2_all  = R2eff_all * C2_all
    with np.errstate(invalid='ignore', divide='ignore'):
        f0_all   = 1.0 / (2*np.pi * np.sqrt(tau1_all * tau2_all))
        zeta_all = (tau1_all + tau2_all) / (2*np.sqrt(tau1_all * tau2_all))

    # 4. Máscara de validez
    f0err_all = np.abs(f0_all - f0_des) / f0_des * 100
    valid_mask = (np.isfinite(f0_all) & np.isfinite(zeta_all) &
                  (f0err_all <= F0_TOL_PCT) &
                  (zeta_all >= ZETA_MIN) & (zeta_all <= ZETA_MAX))

    valid_idx = np.where(valid_mask)[0]
    print(f"    f0/ζ vectorizado: {valid_mask.sum():,} pasan → "
          f"dedup + T-red en {time.time()-_t:.2f}s", flush=True)

    # 5. Deduplicar por (R1, C1, C2, round(R2c,1)) — solo sobre los válidos
    seen_keys = set()
    p2_candidates = []
    for i in tqdm(valid_idx, desc=f"[{label}] T-red+dedup",
                  unit="cand", ncols=90):
        R1=float(R1_all[i]); C1=float(C1_all[i]); C2=float(C2_all[i])
        R2_i=float(R2i_all[i]); R2_c=float(R2c_all[i])

        err_single = abs(R2_c-R2_i)/R2_i*100
        tred_list  = mejores_tred(R2_i, top_n=5)
        tred_best  = tred_list[0] if tred_list else None
        err_tred   = tred_best['err'] if tred_best else np.inf
        if tred_best and err_tred < err_single:
            R2_act=tred_best['R2_eq']; r2_type='T'; r2_err=err_tred
        else:
            R2_act=R2_c; r2_type='S'; r2_err=err_single

        key=(R1, C1, round(C2,30), round(R2_act,1))
        if key in seen_keys: continue
        seen_keys.add(key)

        # Recheck con R2_act real (T-red puede diferir de R2_c)
        f0v,zv = circuito_params(R1, R2_act, C1, C2)
        if f0v is None: continue
        if abs(f0v-f0_des)/f0_des*100 > F0_TOL_PCT: continue
        if not (ZETA_MIN<=zv<=ZETA_MAX): continue

        cand = phase1[i]
        p2_candidates.append(dict(
            R1=R1, C1=C1, C2=C2,
            C2_type=cand['C2_type'], C2_a=cand['C2_a'], C2_b=cand['C2_b'],
            R2_ideal=R2_i, R2_act=R2_act, r2_type=r2_type, r2_err=r2_err,
            tred_list=tred_list, f0=f0v, f0_err=abs(f0v-f0_des)/f0_des*100,
            zeta=zv, Rref=res_comercial(R1*R2_act/(R1+R2_act))
        ))

    print(f"    {len(p2_candidates):,} candidatos válidos en {time.time()-_t:.2f}s",
          flush=True)
    if not p2_candidates: return [], []

    # ── Phase 2 GPU batch ────────────────────────────────────────────────────
    H_bp_all, a_all, b_all = phase2_batch_gpu(p2_candidates, label)

    # ── Adder search per candidate (CPU vectorizado) ─────────────────────────
    print(f"  [{label}] Adder search ({len(p2_candidates):,} cands)...", flush=True)
    _t = time.time()
    verified = []
    for k in tqdm(range(len(p2_candidates)),
                  desc=f"[{label}] Adder", unit="cand", ncols=90):
        cand   = p2_candidates[k]
        H_bp_k = H_bp_all[k].astype(np.complex128)
        adder  = buscar_adder_fast(H_bp_k, a_all[k], b_all[k], top_n=5)
        if not adder: continue
        ba = adder[0]
        cand['err_rms']   = ba['err_rms']
        cand['K_opt_dB']  = ba['K_opt_dB']
        cand['K_opt_lin'] = 10**(ba['K_opt_dB']/20)
        cand['adder_list']= adder
        cand['best_adder']= ba
        verified.append(cand)
    print(f"    {len(verified):,} con adder en {time.time()-_t:.2f}s", flush=True)

    verified.sort(key=lambda x: (x['err_rms'],x['f0_err']))

    # Seleccionar top con diversidad de ζ
    _BANDS = [(100,200),(200,300),(300,400),(400,500),
              (500,600),(600,700),(700,800),(800,900),(900,1001)]
    def _ck(v): return (v['R1'],v['C1'],round(v['C2'],30),round(v['R2_act'],1))
    results=[]; seen_c=set()
    for lo,hi in _BANDS:
        for v in verified:
            if lo<=v['zeta']<hi and _ck(v) not in seen_c:
                results.append(v); seen_c.add(_ck(v)); break
    for v in verified:
        if len(results)>=TOP_N: break
        if _ck(v) not in seen_c: results.append(v); seen_c.add(_ck(v))
    results.sort(key=lambda x: x['zeta'])
    return verified, results

# ════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN
# ════════════════════════════════════════════════════════════════════════════

_t_total = time.time()
verified_todos,     results_todos     = buscar_gpu(CAPS_TODOS,     _C2_EXT_TODOS, "TODOS")
verified_ceramicos, results_ceramicos = buscar_gpu(CAPS_CERAMICOS, _C2_EXT_CERAM, "CERÁMICOS")
print(f"\n  Total: {time.time()-_t_total:.1f}s", flush=True)

GRUPOS = [
    ("TODOS LOS CAPACITORES",    "todos_gpu",    verified_todos,     results_todos),
    ("SOLO CERÁMICOS (≤ 100nF)", "ceramicos_gpu",verified_ceramicos, results_ceramicos),
]

# ════════════════════════════════════════════════════════════════════════════
# OUTPUT, CSV, GRÁFICAS
# ════════════════════════════════════════════════════════════════════════════
import matplotlib.pyplot as plt

SEP        = "="*150; sep = "-"*150
script_dir = os.path.dirname(os.path.abspath(__file__))
freqs_plot = np.logspace(-3,6,3000)
colores    = ['royalblue','forestgreen','firebrick','darkorange',
              'purple','teal','brown','crimson']

for grupo_label, grupo_slug, verified, results in GRUPOS:
    if not results:
        print(f"\n  [{grupo_label}] Sin resultados.", flush=True)
        continue

    print(); print(SEP)
    print(f"  GRUPO: {grupo_label}  [Backend GPU: {BACKEND}]")
    print(f"  H_ideal=(s²+2ζ₀ω₀s+ω₀²)/(s²+2ζ₁ω₀s+ω₀²)  "
          f"ζ₀={ZETA_GEO}  ζ₁={ZETA_TARGET}  f0={f0_des}Hz")
    print(SEP)
    _C2T = {'S':' ','P':'P','E':'E'}
    hdr=(f"{'#':<3} {'C1':>8} {'C2':>10} {'R1':>10} {'R2_act':>11}"
         f"  {'f0':>7} {'ζ':>8} {'f0err%':>7}"
         f"  {'errRMS_dB':>10} {'K_opt_dB':>9}"
         f"  {'Ru':>10} {'Rbp':>10} {'Rf':>10}"
         f"  {'a=Rf/Ru':>8} {'b=Rf/Rbp':>9}")
    print(hdr); print(sep)
    for i,r in enumerate(results):
        ba=r['best_adder']; c2t=_C2T.get(r['C2_type'],' ')
        print(f"{i+1:<3} {fmt_cap(r['C1']):>8} {fmt_cap(r['C2']):>9}{c2t}"
              f" {fmt_res(r['R1']):>10} {fmt_res(r['R2_act']):>11}"
              f"  {r['f0']:>7.3f} {r['zeta']:>8.2f} {r['f0_err']:>6.2f}%"
              f"  {r['err_rms']:>9.4f}dB {r['K_opt_dB']:>+9.2f}dB"
              f"  {fmt_res(ba['Ru']):>10} {fmt_res(ba['Rbp']):>10}"
              f" {fmt_res(ba['Rf']):>10}"
              f"  {ba['a_real']:>8.4f} {ba['b_real']:>9.4f}")
    print(f"\n  (P=C2 paralelo  E=C2 serie)")

    # ── CSV ────────────────────────────────────────────────────────────────────
    csv_path = os.path.join(script_dir, f"compensador_{grupo_slug}.csv")
    with open(csv_path,'w',newline='',encoding='utf-8') as f:
        w=csv.writer(f)
        w.writerow(["rank","C1_F","C2_F","C2_type","C2_a_F","C2_b_F",
                    "R1_ohm","R2_ideal_ohm","R2_act_ohm","Rref_ohm",
                    "f0_Hz","f0_err_pct","zeta","err_rms_dB",
                    "K_opt_dB","K_opt_lin","R2_type","R2_err_pct",
                    "Ru_ohm","Rbp_ohm","Rf_ohm","a_real","b_real"])
        for i,r in enumerate(results):
            ba=r['best_adder']
            w.writerow([i+1,r['C1'],r['C2'],r['C2_type'],
                        r['C2_a'] or '',r['C2_b'] or '',
                        r['R1'],r['R2_ideal'],r['R2_act'],r['Rref'],
                        r['f0'],round(r['f0_err'],4),round(r['zeta'],4),
                        round(r['err_rms'],6),round(r['K_opt_dB'],4),
                        round(r['K_opt_lin'],6),r['r2_type'],round(r['r2_err'],4),
                        ba['Ru'],ba['Rbp'],ba['Rf'],
                        round(ba['a_real'],6),round(ba['b_real'],6)])
    print(f"\n  CSV: {csv_path}", flush=True)

    # ── Gráfica ─────────────────────────────────────────────────────────────────
    H_id_plt = H_ideal_arr(freqs_plot)
    mag_id   = 20*np.log10(np.abs(H_id_plt))
    N_PLOT   = min(5, len(results))
    fig,(ax_mag,ax_err)=plt.subplots(2,1,figsize=(15,10),sharex=True)
    ax_mag.semilogx(freqs_plot,mag_id,'k--',lw=2.5,label='H_ideal')
    for i in range(N_PLOT):
        r=results[i]; col=colores[i%len(colores)]; ba=r['best_adder']
        s_plt=1j*2*np.pi*freqs_plot
        nif_plt=(1+s_plt/omega_p_add)/A0_add
        H_t=H_real_arr(r['R1'],r['R2_act'],r['C1'],r['C2'],freqs_plot)
        a=ba['Rf']/ba['Ru']; b=ba['Rf']/ba['Rbp']; Rf=ba['Rf']
        H_tot=(-a/(1+nif_plt*(1+a+Rf/Rin_add)))+(-b/(1+nif_plt*(1+b+Rf/Rin_add)))*H_t
        mK=20*np.log10(np.abs(H_tot))+r['K_opt_dB']
        lbl=(f"#{i+1} ζ={r['zeta']:.0f} R1={fmt_res(r['R1'])} "
             f"C1={fmt_cap(r['C1'])} C2={fmt_cap(r['C2'])} err={r['err_rms']:.2f}dB")
        ax_mag.semilogx(freqs_plot,mK,color=col,lw=1.8,label=lbl)
        ax_err.semilogx(freqs_plot,mK-mag_id,color=col,lw=1.5,
                        label=f"#{i+1} ζ={r['zeta']:.0f}")
    ax_mag.axvline(f0_des,color='gray',ls=':',lw=1)
    ax_mag.set_ylabel('Magnitud (dB)'); ax_mag.legend(loc='lower right',fontsize=6)
    ax_mag.set_title(f'[{grupo_label}] GPU ({BACKEND}) — K·|H_total| vs H_ideal')
    ax_mag.grid(True,which='both',ls=':',alpha=0.4)
    ax_mag.set_xlim([freqs_plot[0],freqs_plot[-1]])
    ax_err.axhline(0, color='k', lw=0.8); ax_err.axvline(f0_des, color='gray', ls=':', lw=1)
    ax_err.set_xlabel('Frecuencia (Hz)'); ax_err.set_ylabel('Error (dB)')
    ax_err.legend(fontsize=8); ax_err.grid(True,which='both',ls=':',alpha=0.4)
    plt.tight_layout()
    plt.savefig(os.path.join(script_dir,f"compensador_{grupo_slug}.png"),dpi=150)
    plt.show()
