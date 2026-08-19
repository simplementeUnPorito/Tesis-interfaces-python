# TAREAS — Kalman+RTS y picking de dispersión

Checklist vivo. Contexto y decisiones en **`HANDOFF_KALMAN.md`**; bibliografía en
**`REFERENCIAS_KALMAN.md`**.

## Regla dura

**Una fila no se marca hecha sin pegar la salida numérica de su comando en la
bitácora del `HANDOFF_KALMAN.md` (§12).**

Es lo que permite que otra sesión u otro agente confíe en el estado sin
re-verificar todo. Un ⬜ que se convierte en ✅ sin evidencia no vale nada, y
peor: hace que el siguiente construya sobre arena.

Leyenda: ⬜ pendiente · 🔄 en curso · ✅ verificado con salida pegada · ⛔ bloqueado

Prefijo común de todos los comandos:

```bash
cd C:/Github/Tesis/src/interfaces/python
CLI="python -m geophone_scope.kalman_deconv.cli"
```

---

## S1 — Modelo, biblioteca, realización, reducción

| # | Función / entregable | Archivo | Comando de verificación | Criterio numérico | Estado |
|---|---|---|---|---|---|
| 1.1 | dataclasses de configuración (todas las opciones futuras de UI) | `models.py` | `$CLI verify-model --dump-config` | Imprime la config completa (fs, dt, Nyquist, geófono, acondicionador, modelo de entrada, ruido, reducción); ningún parámetro hardcodeado fuera de dataclass | ✅ |
| 1.2 | `list/load/save_geophone`, `list/load/save_conditioner` | `library.py` | `$CLI models list` | Lista ≥ 3 geófonos y ≥ 5 acondicionadores; `save` + `load` da *round-trip* idéntico | ✅ |
| 1.3 | Catálogo congelado con procedencia | `data/geophones/*.json`, `data/conditioners/*.json` | `$CLI models show lp_pga_medido` | Contiene polos/ceros, banda de validez, y el **hash del `informe_identificacion.txt`** | ✅ |
| 1.4 | `geophone_zpk` (forma **aceleración**) | `plant.py` | `$CLI verify-model --stage geophone` | **1** cero en s=0; y ζ 0,25→0,60 mueve la **asíntota de alta frecuencia** (≥500 Hz) menos de 0,05 dB. **Corregido**: el criterio NO puede evaluarse en 25 Hz — ahí el ζ del *denominador* mueve \|H\| ~1 dB, y eso es física. La firma de acoplamiento de normalización sería +7,60 dB | ✅ |
| 1.5 | `compose_plant_zpk` — GEO × CONDITIONER | `plant.py` | `$CLI verify-model --cond comp_nominal --stage compose` | **3 ceros / 5 polos, grado relativo 2**; polos en −0,0342 / −15,708±60,84j / −2469 / −120207 rad/s; ripple ≤ 0,6 dB entre 0,1 y 100 Hz. **Corregido**: la cancelación geófono↔compensador NO es exacta (ver bitácora 2026-08-17 S1) | ✅ |
| 1.6 | **Validación externa contra Ma et al. 2023** | `plant.py` | `$CLI validate-external` | Ripple ≤ 1,5 dB en 1–100 Hz **y** corte en el analítico ω₀/(2ζ₁) = 0,50 Hz ±15 %. **Corregido**: el paper informa 0,8–0,9 Hz *medidos*; contra el analítico hay la misma brecha nominal-vs-medido de siempre. **Si falla, el constructor está mal y todo lo demás es inútil** | ✅ |
| 1.7 | `zpk_to_modal_ss` — realización modal bloque-diagonal | `plant.py` | `$CLI verify-model --stage modal` | ≤ 0,10 dB y ≤ 1,0° máx en 1–150 Hz vs el zpk (se obtiene 0,0000 dB / 0,000°). **Corregido**: se quitó el criterio `cond(A) < 1e5` — no es el indicador correcto y el nominal da 3,5e6 reproduciendo el zpk exactamente. `cond(A)` se **reporta**, no se usa como gate | ✅ |
| 1.8 | `prune_near_cancellations` | `reduce.py` | `$CLI verify-model --cond lp_pga_medido --stage prune` | Con `tol = 5e-3`: poda **exactamente 2** pares conjugados (258 Hz, δ=2,8e−4; y 0,26 Hz, δ=2,5e−3), orden 13→9; Δ ≤ 0,05 dB en 10–50 Hz; devuelve el log. **Y NO poda** el dipolo de desintonía del nominal (δ=2,05e−2) | ✅ |
| 1.9 | `reflect_rhp_zeros` — cero RHP fuera de banda | `reduce.py` | `$CLI verify-model --cond lp_pga_medido --stage prune --prune-rhp` | **Refleja, no borra**: +0,0605 → −0,0605 rad/s. Δ ≤ 0,05 dB en 10–50 Hz (se obtiene 0,0001 dB) y fase ≤ 0,5° (0,063°). **Corregido**: borrarlo equivale a quitar un derivador y desvía 74 dB; ver bitácora | ✅ |
| 1.10 | `residualize_fast_modes` | `reduce.py` | `$CLI verify-model --cond lp_pga_medido --stage residualize --fs 1020` | Corte en **0,9·π·fs** (no 0,4: ver bitácora). Elimina solo el par de 2728 Hz; ningún \|λ\| > 0,8·π·fs; ≤ 0,30 dB / 2° en 1–150 Hz para las tres fs | ✅ |
| 1.11 | `verify_frf` + tabla de aceptación | `reduce.py` | `$CLI verify-model --fs 2604 --report` | Imprime la tabla completa; todas las filas PASS | ✅ |
| 1.12 | Chequeo cruzado `GEO_LP` vs `Hgeo·LP_PGA` | `plant.py` | `$CLI verify-model --stage crosscheck` | Coinciden dentro de 0,50 dB / 3,0° en 1–150 Hz. **Si no cierra, hay error de normalización — S1 no termina** | ✅ |
| 1.13 | Higiene numérica | — | `grep -rn "np.roots\|tf2ss" plant.py reduce.py cli.py` | **Cero coincidencias.** Las cuadráticas usan `second_order_poles` (forma cerrada, estable con ζ≫1); los polinomios de orden 11/13 se factorizan una sola vez en `build_catalog.py` | ✅ |
| 1.14 | **Conversión de magnitud de entrada** (`_magnitude_change`) | `plant.py` | `python -m unittest geophone_scope.test_kalman_plant_magnitude` | `H_v/H_a = jω` y `H_d/H_a = (jω)²` con error relativo **< 1e−12**; el cociente es un derivador (+90,000° y +20 dB/déc); el grado relativo **baja** uno por nivel; combinación impropia (geófono solo + displacement) falla explícita. **Corrige el bug de los polos en el origen**; ver `reports/velocity_model_fix_2026-08-17/` | ✅ |

## S2 — Discretización, KF+RTS, banco sintético

| # | Función / entregable | Archivo | Comando de verificación | Criterio numérico | Estado |
|---|---|---|---|---|---|
| 2.1 | `discretize_plant` | `discretize.py` | `$CLI verify-model --stage discrete --fs 2604` | ≤ 0,30 dB / 2° hasta 0,4·Nyquist, para **1020, 2604 y 2929** | 🔄 |
| 2.2 | Guarda anti-aliasing (test negativo) | `discretize.py` | `$CLI verify-model --stage discrete --fs 1020 --no-residualize` | Lanza `ValueError`. **Falla ruidosa, nunca aliasing silencioso** | ✅ |
| 2.3 | **TEST 1** — parámetros de Markov | `discretize.py` | `$CLI markov --cond comp_nominal` / `--cond lp_pga_medido` | Correr sobre las matrices **continuas** (A,B,C), o con umbral **relativo**. ⚠ Bajo ZOH los `CᵈAᵈⁱBᵈ` **nunca son exactamente cero**: para grado relativo r valen ≈(Tʳ/r!)·CA^(r−1)B, chicos pero no nulos, y un test de cero exacto encuentra i=0 y «falla» sin motivo. Esperado: r=2 (nominal), r=4 (medido) → L mínimo | ✅ |
| 2.4 | `build_input_model` (`leaky_rw`, `random_walk`, `wiener2`, `ou_band`) | `discretize.py` | `$CLI show-input-model --input-model leaky_rw` | Respuesta plana en 10–50 Hz y acotada en DC | 🔄 |
| 2.5 | `augment_with_input_model` + `build_process_noise` (Van Loan) | `discretize.py` | `$CLI verify-model --stage augment --fs 1020 --fs 2929` | El mismo `q_scale` da la misma densidad continua a las dos fs (independencia de fs) | ✅ |
| 2.6 | **TEST 2** — `check_observability` (PBH + gramiano) | `discretize.py` | `$CLI check-obsv --input-model random_walk` | **Debe reportar NO observable** en z=1. Si no lo detecta, el chequeo no sirve | ✅ |
| 2.7 | **TEST 2** — caso que sí pasa | `discretize.py` | `$CLI check-obsv --input-model leaky_rw` | **Debe reportar observable**; imprime la dirección más débil y a qué modo corresponde | ✅ |
| 2.7b | **TEST 2 en la planta de VELOCIDAD** — no se hereda | `discretize.py` | `$CLI check-obsv --cond lp_pga_medido --estimate velocity --input-model leaky_rw --fs 1020` | Observable (8/8) con `leaky_rw`, no observable (7/8) con `random_walk`. **Pero el margen PBH cae de 2,111e−2 a 4,024e−5 (~500×)**: el doble cero en el origen degrada la observabilidad en DC y es la causa medida de la deriva sub-1 Hz | ✅ |
| 2.8 | **TEST 3** — ceros de muestreo | `discretize.py` | `$CLI sampling-zeros --fs 1020 --fs 2604 --fs 2929` | Clasifica cada cero vs \|z\|=1 y separa intrínsecos de ceros de muestreo. **Reportar si alguna fs se comporta mejor** | ✅ |
| 2.9 | `kf_forward` (Joseph, simetrización, NaN) | `kf.py` | `$CLI bench --case ricker25 --no-smoother` | `min eig(P) > 0` en todos los pasos y las tres fs; NIS dentro del IC 95 % | 🔄 |
| 2.10 | Robustez a NaN | `kf.py` | `$CLI bench --case ricker25 --nan-frac 0.05` | Sin NaN en la salida; solo predicción en las muestras faltantes | ✅ |
| 2.11 | `rts_backward` | `kf.py` | `$CLI bench --case ricker25 --fs 2604 --snr 20` | `rmse_aligned` ≤ 8 % del RMS de la verdad; amplitud ±10 %; fase < 10° en 10–50 Hz | ✅ |
| 2.12 | RTS mejora sobre KF solo | `kf.py` | `$CLI bench --case ricker25 --compare-smoother` | Mejora ≥ 30 % en `rmse_aligned` | ✅ |
| 2.13 | `forward_simulate(use_full_model=True)` | `synthetic.py` | `$CLI bench --case ricker25 --full-model-sim` | Simula con el modelo **sin reducir** y estima con el reducido, para medir error de **modelo** y no solo de ruido | ✅ |
| 2.14 | `test_nmp_recovery` — inverso causal vs KF vs KF+RTS | `synthetic.py` | `$CLI bench --case nmp` | El inverso directo **diverge** (norma crece >10× al duplicar N); KF+RTS acotado. Demuestra empíricamente §Obstrucción 3 | ✅ |
| 2.15 | `P0` estacionaria, no `1e6·I` | `kf.py` | `$CLI bench --case ricker25 --report-burnin` | Sin rampa de baja frecuencia inicial; `burn_in` reportado | ✅ |

## S3 — Q y R, anti-invención, métricas

| # | Función / entregable | Archivo | Comando de verificación | Criterio numérico | Estado |
|---|---|---|---|---|---|
| 3.1 | `estimate_r_from_pre_arrival` | `estimate.py` | `$CLI fit-noise --bench` | R dentro de 3 dB del oráculo. Se documenta que es **cota superior** | ⬜ |
| 3.2 | `fit_q_scale_ml` | `estimate.py` | `$CLI fit-noise --bench --snr 10 --snr 20 --snr 30` | Recupera el `q_scale` verdadero dentro de **media década** a los 3 SNR | ⬜ |
| 3.3 | `l_curve_q` como chequeo cruzado | `estimate.py` | `$CLI fit-noise --bench --l-curve` | ML y L-curve dentro de **una década**; si no, reporta y toma el más conservador | ⬜ |
| 3.4 | Q desde tolerancias (Monte Carlo) | `estimate.py` | `$CLI fit-noise --from-tolerances` | Usa 4000 muestras, semilla 2909, R ±1 %, cerámicos ±20 %, electrolíticos −40/+10 %; mapea la dispersión de la FRF a Q | ⬜ |
| 3.5 | Anti-invención 1 — LF-null | `estimate.py` | `$CLI no-invent --test lf-null` | `u_hat` ≥ 20 dB atenuado en 0–10 Hz vs 10–50 Hz; **sin pico coherente en 1–2,5 Hz** | ⬜ |
| 3.6 | Anti-invención 2 — ruido puro | `estimate.py` | `$CLI no-invent --test noise-only` | PSD dentro de ±3 dB de la analítica; ningún bin > 10 dB sobre la mediana local | ⬜ |
| 3.7 | Anti-invención 3 — blancura + NIS | `estimate.py` | `$CLI no-invent --test whiteness` | Ljung-Box p > 0,01; NIS dentro del IC 95 % de χ² | ⬜ |
| 3.8 | Anti-invención 4 — energía por tercio de octava | `estimate.py` | `$CLI no-invent --test band-energy` | ±1,5 dB en 10–50 Hz; **atenuado** bajo 0,211 Hz | ⬜ |
| 3.9 | Anti-invención 5 — transferencia efectiva `H_p̂d·H_dp` | `estimate.py` | `$CLI no-invent --test effective-tf --plot` | Produce el gráfico dato-vs-prior. *Es el que responde «¿no estás inventando?»* | ⬜ |
| 3.10 | Anti-invención 6 — PSD del error (Maes Ec. 70) | `estimate.py` | `$CLI no-invent --test error-psd` | PSD analítica del error superpuesta a la de la señal; marca las bandas no reportables | ⬜ |
| 3.11 | Anti-invención 7 — entrada nula | `estimate.py` | `$CLI no-invent --test null-input --capture <ruta>` | Sin estructura espectral. **Documentar si se usó fondo real o el fallback pre-trigger** | ⬜ |
| 3.12 | Anti-invención 8 — barrido de Q | `estimate.py` | `$CLI no-invent --test q-sweep` | Insensible en 10–50 Hz sobre 2–3 décadas; sensible bajo 10 Hz | ⬜ |
| 3.13 | **Test negativo de la suite** | `estimate.py` | `$CLI no-invent --force-q-scale 100x` | La suite **debe FALLAR**. Si pasa, la suite no mide nada | ⬜ |
| 3.14 | `compare_methods` — 5 brazos | `metrics.py` | `$CLI compare --bench` | Brazos: crudo / Butterworth / **deconvolución instrumental directa** / KF / KF+RTS. `lag_KF > 0`, `lag_RTS ≈ 0` | ⬜ |
| 3.15 | Métrica de honestidad 1–10 / 10–50 Hz | `metrics.py` | `$CLI compare --bench --energy-ratio` | Reporta la relación por brazo; fija el umbral X que usará S4 | ⬜ |

## S4 — Datos reales

| # | Entregable | Comando de verificación | Criterio | Estado |
|---|---|---|---|---|
| 4.1 | Corrida sobre capturas reales | `$CLI run --capture <ruta>` | ≥ 5 capturas de **2 fs distintas**, sin excepciones y sin NaN | ⬜ |
| 4.2 | Consistencia de R entre capturas | `$CLI fit-noise --campaign Canchita_grupo1_procesado` | Dispersión < 6 dB dentro del mismo sitio; outliers listados | ⬜ |
| 4.3 | **Discriminación nominal vs medido** | `$CLI compare --capture <ruta> --cond comp_nominal --cond lp_pga_medido` | `comp_nominal` **falla** blancura y reconstrucción; `lp_pga_medido` pasa. **Es el resultado esperado, no un bug** | ⬜ |
| 4.4 | Reporte por captura | `$CLI report --campaign <nombre>` | Tabla de brazos + PSD + PSD del error por Ec. (70) + banda ±2σ | ⬜ |
| 4.5 | Verificación de honestidad bajo 10 Hz | `$CLI report --campaign <nombre> --honesty` | Si la relación de energía supera el umbral de 3.15, se marca **«no confiable bajo 10 Hz»** — resultado esperado y publicable | ⬜ |
| 4.6 | Coherencia entre canales | `$CLI compare --campaign <nombre> --coherence` | Mejora o se mantiene vs el brazo Butterworth en 10–50 Hz | ⬜ |

## S5 — Integración web (tab Filtros)

| # | Entregable | Verificación | Criterio | Estado |
|---|---|---|---|---|
| 5.1 | Función adaptadora contra `field_review_data` | test de import | Sin dependencia de Qt ni FastAPI en el módulo núcleo. **Import blando**: si `kalman_deconv` no está, `AVAILABLE=False` y el servidor arranca igual | ✅ |
| 5.2 | Backend `server/kalman.py` + `server/routers/kalman.py` | `python server/smoke_test.py` | 57/58. El único FAIL es `masw.canchita_compatibilidad`, **ajeno a este trabajo**: el check espera 112 picks y los datos tienen 113 (ver bitácora 2026-08-18) | ✅ |
| 5.3 | Tab JS con controles | manual + navegador | Combos de GEO y CONDITIONER, magnitud, prior de entrada con su banda/fuga, discretización, origen de Q y de R, pasa-banda posterior. **Todo opcional y apagado por defecto** | ✅ |
| 5.4 | Estado compartido | revisión de código | **Todo write pasa por `server/state.py`** (`locked` + `require_revision` + `atomic_write_json`). Verificado: 409 ante revisión obsoleta, 400 ante magnitud inválida | ✅ |
| 5.5 | La funcionalidad es **opcional**, no obligatoria | navegador | Con el maestro apagado: **0 pedidos** a `/api/kalman/preview`, gráfico oculto y el pasa-banda se comporta igual que antes de existir el módulo | ✅ |
| 5.6 | Overlay opcional de la ventana Kalman en MASW | navegador | Checkbox apagado por defecto; prendido dibuja las dos envolventes de la región admisible. **No es un picking** y no se exporta como tal | ✅ |
| 5.7 | Suavizador **RTS** conmutable desde la web | navegador | Checkbox apagado por defecto. Prendido, la energía en 10–50 Hz sube de 0,765 a **0,875** en la captura de prueba y el rótulo del gráfico pasa a decir «RTS» | ✅ |
| 5.8 | Las cuatro máscaras, cada una por separado | navegador | `layers`: `physical`, `kalman_gate`, `energetic`, `combined`, con su envolvente propia. Tres checkboxes independientes; con todas apagadas no se pide nada | ✅ |
| 5.9 | Curva externa de referencia como overlay | `GET /api/kalman/reference` | 132 puntos, 8,00–29,83 Hz, en `cR` y en `Vs_app = cR/0,92`. `enters_computation: false`. Si el archivo no está, `available: false` en vez de fallar | ✅ |
| 5.10 | Imagen MASW calculada **desde `v_ground`** | `POST /api/kalman/masw-dispersion` | Mismo contrato que `/api/masw/dispersion` (incluido `image_png`). 21/21 canales finitos, NIS medio 0,88. **3 min 24 s**: es un botón explícito, con aviso en la interfaz | ✅ |
| 5.11 | Origen de Q seleccionable, con el costo declarado | catálogo | `ml_reference` (un ajuste, escalado por R — el único practicable para la imagen completa), `ml` (canal por canal) y `manual`. El ajuste usa una ventana de 4 s: acota el costo de la **búsqueda**, no el del filtro | ✅ |

## S6 — Picking de dispersión

| # | Entregable | Verificación | Criterio | Estado |
|---|---|---|---|---|
| 6.1 | **Medir el jitter de trigger** — hacer esto PRIMERO | script de análisis | Cuantificado en ms y comparado contra el período a 50 Hz (20 ms). Condiciona todo lo demás | ⬜ |
| 6.2 | PWS como peso opcional del barrido | comparación contra phase-shift | Probar μ=1 antes que μ=2; **verificar que no suprime señal real** por el arreglo sintetizado | ⬜ |
| 6.3 | Ridge/Hessiano (Hou et al. 2025) | contra el `argmax` actual | Menos saltos de rama modal | ⬜ |
| 6.4 | Tracker Kalman en lentitud `p = 1/c` | contra `argmax` y ridge | Estado `[p, dp/df]`; `R(f)` desde energía, ancho, coherencia y SNR del pico | 🔄 |
| 6.5 | Cotas del arreglo como *gating* de la innovación | test sobre gather sintético | `c ≥ 2Δx·f` y `c ≤ L·f` respetadas por construcción. **Es el aporte más defendible** | ✅ |
| 6.6 | Modo predictivo (acota la ventana de búsqueda del bin siguiente) | contra el modo suavizador | Robusto a saltos de modo donde el `argmax` falla | ✅ |
| 6.7 | σ(f) exportado como pesos de datos | integración con `masw_backends` | La inversión los consume | ⬜ |

## S6b — Benchmarks obligatorios

| # | Entregable | Criterio | Estado |
|---|---|---|---|
| 6b.1 | HLRT (Luo et al. 2008) | Implementado y comparado sobre las mismas capturas | ⬜ |
| 6b.2 | Sparse L1 (Mun et al. 2015) | Idem | ⬜ |
| 6b.3 | E-DBSCAN (solo la modificación energética) | Idem | ⬜ |
| 6b.4 | Conclusión documentada | **Resultado esperado dado Shen et al. 2015: mejora marginal o nula.** Documentarlo así es el entregable — un resultado negativo bien medido es publicable; omitir la comparación no | ⬜ |

## S7 — Inversión y comparación contra la referencia

| # | Entregable | Criterio | Estado |
|---|---|---|---|
| 7.1 | `ĉ_R(f)` contra `Moldeo Hidro` | RMS en el solape 8,0–29,8 Hz | ✅ |
| 7.2 | `Vs(z)` contra el modelo de 7 capas | Comparación por cotas de litología | ⬜ |
| 7.3 | Inversión ponderada por σ(f) | `evodcinv`/`disba` consumen los pesos | ⬜ |
| 7.4 | Conclusión global | ¿La cadena KF+RTS → MASW → inversión acerca el `Vs(z)` a la referencia, o no? **Las dos respuestas son resultados** | ⬜ |

---

## Pendientes sueltos

- [ ] Decidir la rama de trabajo. El superproyecto está en `cambios-hardware`;
      este submódulo estaba en `main` y limpio al arrancar. Convención del
      proyecto: **el mismo nombre de rama en el superproyecto y en cada
      submódulo que se toque**.
- [ ] Congelar el catálogo `lp_pga_medido` con el **hash** del
      `informe_identificacion.txt`, para que un cambio de la fuente se detecte.
- [ ] Confirmar si existe algún registro de ruido de fondo sin martillo en
      campañas futuras (hoy no hay — ver `HANDOFF_KALMAN.md` §5.6).
- [ ] Verificar las citas marcadas **PROBABLE** en `REFERENCIAS_KALMAN.md`
      antes de usarlas en la tesis (Shen et al. 2015; Devasia/Bayo/Chen;
      Floquet & Barbot; Hsieh; la literatura de Tikhonov/curva-L).
- [ ] Calcular numéricamente los ceros límite de muestreo en vez de citar el
      polinomio de Euler–Frobenius (marcado **NO VERIFICADO**).
