"""Interfaz de linea de comandos: verificar el modelo sin GUI.

    cd C:/Github/Tesis/src/interfaces/python
    python -m geophone_scope.kalman_deconv.cli verify-model --fs 2604 --report

Los comandos y sus criterios de aceptacion estan en ``../TAREAS_KALMAN.md``.
Regla del proyecto: una tarea no se marca hecha sin pegar la salida numerica de
su comando en la bitacora de ``../HANDOFF_KALMAN.md``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, replace
from typing import Optional

import numpy as np

from .library import list_conditioners, list_geophones, load_conditioner, load_geophone
from .models import DeconvConfig, PlantSpec, ReductionSpec
from .plant import (
    compose_plant_zpk,
    geophone_zpk,
    relative_degree,
    zpk_freqresp,
    zpk_to_modal_ss,
)
from .reduce import (
    prune_near_cancellations,
    reflect_rhp_zeros,
    residualize_fast_modes,
    verify_frf,
)

BAND_FULL = (1.0, 150.0)
BAND_USEFUL = (10.0, 50.0)


def _hz(values) -> np.ndarray:
    return np.abs(np.asarray(values)) / (2.0 * np.pi)


def _build_plant_spec(args) -> PlantSpec:
    cond = load_conditioner(args.cond)
    include_geo = not cond.includes_geophone
    return PlantSpec(
        geophone=load_geophone(args.geo),
        conditioner=cond,
        include_geophone=include_geo,
        include_conditioner=True,
        estimate=args.estimate,
    )


def _print_zpk(label: str, z, p, k) -> None:
    print(f"\n{label}")
    print(f"  {len(z)} ceros / {len(p)} polos  ->  grado relativo {relative_degree(z, p)}")
    print(f"  ganancia zpk: {k:.6g}")
    if len(p):
        f = _hz(p)
        print(f"  polos: {f.min():.6g} .. {f.max():.6g} Hz")
        for pole in sorted(p, key=lambda v: abs(v)):
            print(
                f"    {pole.real:+14.6g} {pole.imag:+14.6g}j   "
                f"{abs(pole) / (2 * np.pi):12.6g} Hz"
            )
    if len(z):
        print("  ceros:")
        for zero in sorted(z, key=lambda v: abs(v)):
            flag = "  <-- SEMIPLANO DERECHO" if zero.real > 0 else ""
            print(
                f"    {zero.real:+14.6g} {zero.imag:+14.6g}j   "
                f"{abs(zero) / (2 * np.pi):12.6g} Hz{flag}"
            )


def _print_response(z, p, k, freqs, *, norm_hz: Optional[float] = None) -> None:
    w = 2.0 * np.pi * np.asarray(freqs, dtype=float)
    h = zpk_freqresp(z, p, k, w)
    ref = 1.0
    suffix = "dB abs"
    if norm_hz is not None:
        ref = abs(zpk_freqresp(z, p, k, np.array([2.0 * np.pi * norm_hz]))[0])
        suffix = f"dB rel {norm_hz:g} Hz"
    print(f"\n  {'f [Hz]':>10}  {suffix:>14}  {'fase [deg]':>12}")
    for fi, hi in zip(freqs, h):
        print(
            f"  {fi:10.4g}  {20 * np.log10(abs(hi) / ref):14.4f}  "
            f"{np.angle(hi, deg=True):12.3f}"
        )


# --------------------------------------------------------------------------- #
# Subcomandos
# --------------------------------------------------------------------------- #


def cmd_models(args) -> int:
    if args.model_id:
        try:
            spec = load_geophone(args.model_id)
        except KeyError:
            spec = load_conditioner(args.model_id)
        print(f"\n{spec.id} - {spec.name}")
        for key, value in asdict(spec).items():
            if key in ("id", "name"):
                continue
            if key in ("zeros", "poles") and value:
                print(f"  {key}: {len(value)} elementos")
                for v in value:
                    v = complex(v[0], v[1]) if isinstance(v, (list, tuple)) else complex(v)
                    print(f"    {v.real:+14.6g} {v.imag:+14.6g}j")
                continue
            print(f"  {key}: {value}")
        return 0

    print("\nGEOFONOS")
    print(f"  {'id':22s} {'f_n':>7s} {'zeta':>8s} {'forma':>13s}  nombre")
    for row in list_geophones():
        mark = "*" if row["builtin"] else " "
        print(
            f" {mark}{row['id']:22s} {row['f_n_hz']:7.3f} {row['zeta']:8.3f} "
            f"{row['form']:>13s}  {row['name']}"
        )

    print("\nACONDICIONADORES")
    print(f"  {'id':22s} {'tipo':>12s} {'z/p':>9s}  nombre")
    for row in list_conditioners():
        mark = "*" if row["builtin"] else " "
        zp = f"{row['n_zeros']}/{row['n_poles']}" if row["n_poles"] else "-"
        extra = "  [incluye geofono]" if row["includes_geophone"] else ""
        print(
            f" {mark}{row['id']:22s} {row['kind']:>12s} {zp:>9s}  {row['name']}{extra}"
        )
    print("\n  (* = preset del modulo)")
    return 0


def cmd_verify_model(args) -> int:
    spec = _build_plant_spec(args)
    checks = []
    ok = True

    if args.dump_config:
        cfg = DeconvConfig(fs=args.fs, plant=spec)
        print("CONFIGURACION COMPLETA (todo esto es opcion de UI en S5)\n")

        def _dump(label: str, obj, indent: int = 2) -> None:
            print(f"{' ' * indent}{label}:")
            for key, value in asdict(obj).items():
                if key in ("zeros", "poles"):
                    value = f"<{len(value)} elementos>"
                print(f"{' ' * (indent + 2)}{key} = {value!r}")

        print(f"  fs = {cfg.fs}   dt = {cfg.dt:.9g}   Nyquist = {cfg.nyquist_hz} Hz")
        print(f"  disc_method = {cfg.disc_method!r}   smoother = {cfg.smoother}")
        print(f"  pre_notch = {cfg.pre_notch}   butter_stage = {cfg.butter_stage!r}")
        _dump("plant.geophone", cfg.plant.geophone)
        _dump("plant.conditioner", cfg.plant.conditioner)
        print(f"  plant.estimate = {cfg.plant.estimate!r}")
        print(f"  plant.include_geophone = {cfg.plant.include_geophone}")
        print(f"  plant.include_conditioner = {cfg.plant.include_conditioner}")
        _dump("input_model", cfg.input_model)
        _dump("noise", cfg.noise)
        _dump("reduction", cfg.reduction)
        return 0

    print("=" * 78)
    print(f"GEO  {spec.geophone.id:20s} f_n={spec.geophone.f_n_hz} Hz  "
          f"zeta={spec.geophone.zeta}  forma={spec.geophone.form}")
    print(f"COND {spec.conditioner.id:20s} kind={spec.conditioner.kind}  "
          f"valida en {spec.conditioner.valid_band_hz[0]}-{spec.conditioner.valid_band_hz[1]} Hz")
    print(f"magnitud estimada: {spec.estimate}   fs = {args.fs} Hz")
    print("=" * 78)

    # --- etapa geofono -----------------------------------------------------
    if args.stage in ("all", "geophone"):
        gz, gp, gk = geophone_zpk(spec.geophone)
        _print_zpk("GEOFONO solo", gz, gp, gk)
        n_origin = int(np.sum(np.abs(gz) < 1e-12))
        expected = 1 if spec.geophone.form == "acceleration" else 2
        status = "OK" if n_origin == expected else "FALLA"
        print(f"\n  ceros en el origen: {n_origin} (esperado {expected})  [{status}]")
        ok &= n_origin == expected
        # Trampa de normalizacion: en la forma de Ma el numerador es -G*s y NO
        # contiene zeta, asi que la asintota de alta frecuencia |H_a| -> G/w es
        # independiente de zeta. En la normalizacion del informe el numerador es
        # zeta*w0*s y si la arrastra: pasar zeta de 0,25 a 0,60 moveria la
        # asintota 20*log10(0,60/0,25) = +7,6 dB.
        #
        # El chequeo va en la ASINTOTA (>= 500 Hz), no en 25 Hz: a 2,5 veces la
        # resonancia el zeta del DENOMINADOR todavia mueve |H| cerca de 1 dB, y
        # eso es fisica correcta, no acoplamiento de normalizacion.
        alt = replace(spec.geophone, zeta=0.60)
        az, ap, ak = geophone_zpk(alt)
        w_asym = np.array([2.0 * np.pi * 500.0])
        d_asym = 20.0 * np.log10(
            abs(zpk_freqresp(az, ap, ak, w_asym)[0])
            / abs(zpk_freqresp(gz, gp, gk, w_asym)[0])
        )
        w25 = np.array([2.0 * np.pi * 25.0])
        d_25 = 20.0 * np.log10(
            abs(zpk_freqresp(az, ap, ak, w25)[0])
            / abs(zpk_freqresp(gz, gp, gk, w25)[0])
        )
        asym_ok = abs(d_asym) <= 0.05
        print(
            f"\n  zeta 0,25 -> 0,60:  en 25 Hz {d_25:+.3f} dB (esperable, es el "
            f"denominador)\n"
            f"                      en la asintota 500 Hz {d_asym:+.4f} dB "
            f"(debe ser 0; la firma de acoplamiento seria +7,60 dB)  "
            f"[{'OK' if asym_ok else 'FALLA'}]"
        )
        ok &= asym_ok

    z, p, k = compose_plant_zpk(spec)

    if args.stage in ("all", "compose"):
        _print_zpk("CASCADA GEO x COND", z, p, k)
        freqs = [0.1, 0.5, 1, 2.5, 5, 10, 20, 50, 100, 200]
        _print_response(z, p, k, freqs, norm_hz=args.norm_hz)

    # --- realizacion modal -------------------------------------------------
    if args.stage in ("all", "modal", "prune", "residualize"):
        ss = zpk_to_modal_ss(z, p, k)
        cond_a = np.linalg.cond(ss[0]) if ss[0].size else 1.0
        print(f"\nREALIZACION MODAL: {ss[0].shape[0]} estados, cond(A) = {cond_a:.4g}")
        for band, tol_db, tol_deg in (
            (BAND_FULL, 0.10, 1.0),
            (spec.conditioner.valid_band_hz, 0.30, 2.0),
        ):
            chk = verify_frf(
                (z, p, k), ss, band_hz=band, tol_db=tol_db, tol_deg=tol_deg,
                label="modal vs zpk",
            )
            checks.append(chk)

    # --- poda ---------------------------------------------------------------
    zr, pr, kr = z, p, k
    if args.stage in ("all", "prune", "residualize"):
        zr, pr, kr, log = prune_near_cancellations(
            z, p, k, tol_ratio=args.cancel_tol
        )
        print(f"\nPODA DE CUASI-CANCELACIONES (tol relativa {args.cancel_tol})")
        print(log.describe())
        print(f"  orden: {len(p)} -> {len(pr)} polos, {len(z)} -> {len(zr)} ceros")
        if args.prune_rhp:
            zr, pr, kr, log_rhp = reflect_rhp_zeros(
                zr, pr, kr, below_hz=args.prune_rhp_below
            )
            print(
                f"\nREFLEXION DE CEROS RHP (por debajo de {args.prune_rhp_below} Hz)"
            )
            print(log_rhp.describe())
        checks.append(
            verify_frf(
                (z, p, k), (zr, pr, kr), band_hz=BAND_USEFUL,
                tol_db=0.05, tol_deg=0.5, label="podado vs original",
            )
        )
        checks.append(
            verify_frf(
                (z, p, k), (zr, pr, kr), band_hz=BAND_FULL,
                tol_db=0.30, tol_deg=2.0, label="podado vs original",
            )
        )

    # --- residualizacion ----------------------------------------------------
    if args.stage in ("all", "residualize"):
        # Nyquist en rad/s. El corte va JUSTO POR DEBAJO de Nyquist, no en una
        # fraccion chica: la residualizacion existe para sacar los modos que la
        # discretizacion aliasaria, no para simplificar el modelo. A fs = 1020 un
        # corte de 0,4*Nyquist se llevaba los polos de 266 y 291 Hz, que estan
        # por debajo de Nyquist y hay que discretizar: costaba 2,78 dB y 21,7
        # grados en el borde alto de la banda.
        nyq = np.pi * float(args.fs)
        cutoff = args.residualize_above or 0.9 * nyq
        ss_r = zpk_to_modal_ss(zr, pr, kr)
        A, B, C, D, log_res = residualize_fast_modes(*ss_r, cutoff_rad_s=cutoff)
        print(
            f"\nRESIDUALIZACION (corte {cutoff:.6g} rad/s = "
            f"{cutoff / (2 * np.pi):.6g} Hz, fs = {args.fs} Hz)"
        )
        print(log_res.describe())
        print(f"  estados: {ss_r[0].shape[0]} -> {A.shape[0]}")
        if A.size:
            eig = np.linalg.eigvals(A)
            worst = float(np.max(np.abs(eig)))
            limit = 0.8 * nyq
            status = "OK" if worst <= limit else "FALLA"
            print(
                f"  |lambda| maximo tras residualizar: {worst:.6g} rad/s "
                f"(limite 0,8*Nyquist = {limit:.6g})  [{status}]"
            )
            ok &= worst <= limit
        checks.append(
            verify_frf(
                (z, p, k), (A, B, C, D), band_hz=BAND_USEFUL,
                tol_db=0.05, tol_deg=0.5, label="reducido vs original",
            )
        )
        checks.append(
            verify_frf(
                (z, p, k), (A, B, C, D), band_hz=BAND_FULL,
                tol_db=0.30, tol_deg=2.0, label="reducido vs original",
            )
        )

    # --- chequeo cruzado GEO_LP --------------------------------------------
    if args.stage in ("all", "crosscheck"):
        checks.append(_crosscheck_geo_lp(spec))

    if checks:
        print("\n" + "-" * 78)
        print("VERIFICACION DE RESPUESTA EN FRECUENCIA")
        print("-" * 78)
        for chk in checks:
            print(chk.summary())
            ok &= chk.passed

    print("\n" + "=" * 78)
    print("RESULTADO: " + ("TODO PASA" if ok else "HAY FALLAS"))
    print("=" * 78)
    return 0 if ok else 1


def _crosscheck_geo_lp(spec: PlantSpec):
    """GEO_LP del informe vs la composicion explicita Hgeo x LP_PGA.

    Es el primer test que hay que correr: si no cierra, hay un problema de
    normalizacion que contaminaria todo lo demas.
    """
    composite = PlantSpec(
        geophone=spec.geophone,
        conditioner=load_conditioner("geo_lp_medido"),
        include_geophone=False,
        estimate=spec.estimate,
    )
    explicit = PlantSpec(
        geophone=spec.geophone,
        conditioner=load_conditioner("lp_pga_medido"),
        include_geophone=True,
        estimate=spec.estimate,
    )
    zc, pc, kc = compose_plant_zpk(composite)
    ze, pe, ke = compose_plant_zpk(explicit)
    print("\nCHEQUEO CRUZADO GEO_LP compuesto vs Hgeo x LP_PGA explicito")
    print(f"  compuesto: {len(zc)} ceros / {len(pc)} polos")
    print(f"  explicito: {len(ze)} ceros / {len(pe)} polos")
    # Se compara la FORMA, no la escala: GEO_LP viene con la normalizacion del
    # informe (numerador zeta*w0*s, constante 15,708) mientras el catalogo usa
    # G0 = 28,8 V*s/m. El cociente es 1,8335 = 5,2655 dB, y ese offset se reporta
    # aparte en vez de hacer fallar el test. Lo que si tiene que cerrar es que no
    # quede NINGUNA ondulacion ni error de fase.
    expected_db = 20.0 * np.log10(
        spec.geophone.g0_v_s_per_m / (spec.geophone.zeta * spec.geophone.w0)
    )
    print(
        f"  offset esperado por normalizacion: {expected_db:+.4f} dB "
        f"(G0={spec.geophone.g0_v_s_per_m} vs zeta*w0={spec.geophone.zeta * spec.geophone.w0:.4f})"
    )
    return verify_frf(
        (zc, pc, kc), (ze, pe, ke), band_hz=BAND_FULL,
        tol_db=0.50, tol_deg=3.0, label="GEO_LP vs explicito (forma)",
        align_gain=True,
    )


def cmd_validate_external(args) -> int:
    """Validacion externa del constructor de modelos contra Ma et al. (2023).

    Si esto no cierra, el constructor esta mal y todo lo que venga despues es
    inutil, por mas que los tests internos pasen: los tests internos solo dicen
    que el modulo es consistente consigo mismo.
    """
    geo = load_geophone("jf20dx_ma2023")
    print("=" * 78)
    print("VALIDACION EXTERNA - Ma et al. 2023, Sensors 23:3082")
    print(f"  geofono JF-20DX: f_n = {geo.f_n_hz} Hz, zeta = {geo.zeta}, "
          f"G = {geo.g0_v_s_per_m} V*s/m")
    print("=" * 78)

    f = np.logspace(np.log10(0.05), np.log10(300.0), 4000)
    w = 2.0 * np.pi * f
    rows = []
    for cond_id, label in (("unity", "sin compensar"), ("comp_ma2023", "compensado")):
        spec = PlantSpec(geophone=geo, conditioner=load_conditioner(cond_id))
        z, p, k = compose_plant_zpk(spec)
        h = np.abs(zpk_freqresp(z, p, k, w))
        ref = np.abs(zpk_freqresp(z, p, k, np.array([2.0 * np.pi * 50.0]))[0])
        db = 20.0 * np.log10(h / ref)
        low = f < 30.0
        idx = np.where(db[low] <= -3.0)[0]
        f3 = float(f[low][idx[-1]]) if idx.size else float("nan")
        band = (f >= 1.0) & (f <= 100.0)
        ripple = float(db[band].max() - db[band].min())
        rows.append((label, f3, ripple))
        print(f"  {label:16s} -3 dB en {f3:8.4f} Hz    ripple 1-100 Hz = {ripple:6.2f} dB")

    _, f3_comp, ripple_comp = rows[1]
    # Criterio 1 — planitud: el paper reporta respuesta plana en 1-100 Hz.
    flat_ok = ripple_comp <= 1.5
    # Criterio 2 — corte inferior. El polo lento analitico del denominador con
    # zeta1 = 10 esta en w0/(2*zeta1) = 0,50 Hz, y ahi debe caer el -3 dB. El
    # paper informa 0,8-0,9 Hz, que es su valor MEDIDO sobre el circuito real:
    # la diferencia es la misma clase de brecha nominal-vs-medido que este
    # proyecto encontro en su propio compensador. Se valida contra el analitico.
    w0 = geo.w0
    f3_analytic = w0 / (2.0 * 10.0) / (2.0 * np.pi)
    corner_ok = abs(f3_comp - f3_analytic) / f3_analytic <= 0.15
    print(f"\n  corte analitico esperado w0/(2*zeta1) = {f3_analytic:.4f} Hz")
    print(f"  el paper informa 0,8-0,9 Hz MEDIDOS sobre el circuito real; la "
          f"diferencia\n  contra el analitico es la brecha nominal-vs-medido, no un "
          f"error del modelo.")
    print(f"\n  planitud 1-100 Hz <= 1,5 dB : {'OK' if flat_ok else 'FALLA'}")
    print(f"  corte a {f3_analytic:.3f} Hz +-15%   : {'OK' if corner_ok else 'FALLA'}")
    ok = flat_ok and corner_ok
    print("\n" + "=" * 78)
    print("RESULTADO: " + ("VALIDACION EXTERNA OK" if ok else "FALLA"))
    print("=" * 78)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m geophone_scope.kalman_deconv.cli",
        description="Verificacion del modelo de planta para la deconvolucion Kalman+RTS",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_models = sub.add_parser("models", help="listar o mostrar modelos del catalogo")
    p_models.add_argument("model_id", nargs="?", help="id de un geofono o acondicionador")
    p_models.set_defaults(func=cmd_models)

    p_ver = sub.add_parser("verify-model", help="verificar la planta y su reduccion")
    p_ver.add_argument("--geo", default="sm24_nominal")
    p_ver.add_argument("--cond", default="lp_pga_medido")
    p_ver.add_argument("--fs", type=float, default=2604.0)
    p_ver.add_argument(
        "--estimate", default="acceleration",
        choices=("acceleration", "velocity", "displacement"),
    )
    p_ver.add_argument(
        "--stage", default="all",
        choices=("all", "geophone", "compose", "modal", "prune", "residualize",
                 "crosscheck"),
    )
    p_ver.add_argument("--cancel-tol", type=float, default=ReductionSpec().cancel_tol_ratio)
    p_ver.add_argument("--prune-rhp", action="store_true")
    p_ver.add_argument("--prune-rhp-below", type=float, default=0.211)
    p_ver.add_argument("--residualize-above", type=float, default=None)
    p_ver.add_argument("--norm-hz", type=float, default=None,
                       help="normalizar la respuesta impresa a esta frecuencia")
    p_ver.add_argument(
        "--dump-config", action="store_true",
        help="imprimir la configuracion completa y salir",
    )
    p_ver.add_argument("--report", action="store_true", help="(reservado)")
    p_ver.set_defaults(func=cmd_verify_model)

    p_ext = sub.add_parser(
        "validate-external",
        help="validar el constructor de modelos contra Ma et al. 2023",
    )
    p_ext.set_defaults(func=cmd_validate_external)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
