# Corrección del primer Kalman de velocidad y rehecho del MASW

**Fecha:** 2026-08-17 · **Agente:** Claude (Opus 5) · **Estado:** cerrado, sin commitear.

Este archivo es la **fuente canónica** de la corrección. Está pensado para que una
sesión futura —incluido codex— pueda reproducir todo sin leer la conversación que
lo originó. El `HANDOFF_KALMAN.md` solo lleva un resumen y un enlace acá.

---

## 1. Resumen ejecutivo y conclusión

**Qué estaba mal.** `plant.py::_integrator_chain()` agregaba **polos** en el
origen para cambiar la magnitud de entrada de la planta. Eso construye
`H_a(s)/s`, no `s·H_a(s)`. El estimador que se llamaba «de velocidad» estaba en
realidad estimando la **derivada de la aceleración**, y al invertir ese modelo
realzaba las frecuencias altas en vez de las bajas.

**Qué se hizo.** Se reemplazó por una conversión de magnitud correcta —**ceros**
en el origen, `s^(forma_nativa − magnitud_pedida)`— y se verificó de punta a punta:
identidades algebraicas, regresiones nuevas, los gates de S1/S2 que ya existían,
un caso sintético con verdad conocida, las 21 trazas reales y el PDF.

**Las tres conclusiones que importan:**

1. **La corrección es correcta y está verificada por tres caminos
   independientes.** Las identidades `H_v/H_a = jω` y `H_d/H_a = (jω)²` pasan a
   1e−12; el grado relativo **baja** en uno por cada nivel de derivación (TEST 1
   lo confirma: 4→3 en el medido), que es la firma de un cero y no de un polo; y
   sobre un sintético con verdad conocida el modelo viejo erraba la amplitud por
   un factor **4628** y la fase en **176°**, mientras el corregido da amplitud
   **0,889** y fase **−1,39°**.

2. **El comportamiento espectral ahora es el pedido.** El modelo defectuoso
   movía peso a 50–80 Hz (de 0,0013 a 0,0437 en la corrida vieja; hasta **0,39**
   en canales cercanos). El corregido lo baja a ≤ 0,03 y mueve el centroide de
   **22,07 Hz a 10,27 Hz**. La SNR en la banda útil 10–50 Hz **mejora** de 27,23
   a 30,61 dB y la coherencia entre canales se conserva (0,7316 → 0,7300).

3. **⚠ Y aun así el MASW no mejora — ése es el resultado honesto.** Con la
   planta corregida, `v_ground` concentra el **38,7 %** de su energía por debajo
   de 1 Hz (contra 0,07 % de la aceleración medida): es la deriva de la
   Obstrucción 2 del HANDOFF, ahora peor porque velocidad agrega un **segundo**
   cero en el origen y el margen de observabilidad en DC cae de 2,1e−2 a
   **4,0e−5**. Con el SOS posterior pasa-bajos que se venía usando —que deja
   pasar DC— esa deriva **destruía** la imagen: correlación 0,380, contraste
   1,77 dB. Cambiando el SOS posterior a **pasa-banda 1–80 Hz** la imagen se
   recupera y queda **equivalente** a la de la aceleración medida
   (correlación 0,975; contraste 3,55 vs 3,51 dB; error de máximo local 6,93 vs
   7,24 m/s). **Equivalente, no mejor.**

4. **La segunda iteración sí produce una mejora — y vino del prior, no de la
   planta.** Ver **§10b**. El modelo directo ya era el más detallado del repo; la
   pieza pobre era el **modelo de entrada**. Cambiando `leaky_rw` 0,7 Hz por
   **`ou_band` centrado en 10–50 Hz**, la deriva sub-1 Hz cae de 0,387 a
   **0,0098** (40×), el RMSE sintético de 0,751 a **0,275** (2,7×), y la
   **máscara conjunta del MASW pasa de 6,86–23,71 Hz (60 filas) a
   6,86–46,57 Hz (115 filas)**. `ou_band` quedó como default.

**Conclusión.** La corrección de la planta era necesaria por física, pero por sí
sola solo alcanzaba para **empatar** con `Amedida + SOS`. Lo que compró mejora
fue **poner la banda útil dentro del modelo de entrada** en vez de recortarla con
un filtro después: ahí sí el estimador aporta algo que el filtrado no puede dar,
y casi duplica la banda de frecuencia utilizable. Contra la curva de referencia
externa las métricas siguen siendo un empate técnico —y la referencia solo cubre
8,0–29,8 Hz, así que la ampliación por encima de eso no está validada
externamente—, pero la cobertura en frecuencia es un resultado material para la
inversión posterior. El valor que el HANDOFF §9 ya le atribuía al método —la
covarianza, y poder decir banda por banda cuánto es dato y cuánto supuesto—
sigue en pie y ahora tiene un ejemplo concreto: el control anti-imposición de
§10b muestra que el prior acota la deriva sin dictar el espectro.

---

## 2. Causa raíz, con ecuaciones

La planta directa nativa del proyecto tiene la **aceleración del suelo** como
entrada y volts a la salida:

```
H_a(s) = Y(s)/A_ground(s) = −G·s / (s² + 2ζ₀ω₀s + ω₀²)     [Ma et al. 2023]
```

Como `a_ground = s·v_ground = s²·d_ground`, la planta cuya entrada es la
velocidad de partícula se obtiene por sustitución directa:

```
H_v(s) = Y(s)/V(s) = H_a(s) · A(s)/V(s) = s · H_a(s) = −G·s² / (s² + 2ζ₀ω₀s + ω₀²)
H_d(s) = Y(s)/D(s) = s² · H_a(s)
```

Es decir: **multiplicar**, o sea agregar **ceros** en el origen. El código hacía
lo contrario:

```python
def _integrator_chain(n):
    return np.zeros(0), np.zeros(n), 1.0   # <- n POLOS en s=0
```

lo que construye `H_a(s)/s`. Invertir ese modelo equivale a estimar
`û = s·a_ground` (una derivada más), y por eso el estimador realzaba las
frecuencias altas.

**Corroboración bibliográfica.** Maes et al. (2016) §2.3, ya verificada en
`REFERENCIAS_KALMAN.md`, dice que la transferencia tiene la forma `s^q·(…)` con
**q = 0 para desplazamiento, 1 para velocidad, 2 para aceleración**: el conteo de
ceros en el origen **crece** con el orden de derivación de la magnitud medida.
Nuestro conteo es el mismo desplazado por el propio cero del geófono.

**Firma diagnóstica que quedó registrada.** En la corrida defectuosa el
optimizador de `q` terminó en `q = 98127,9`, o sea `log10 q = 4,99` contra un
límite de búsqueda de `+5,0`: **pegado al borde**. Un optimizador que se raja
contra su cota junto con un NIS medio de 3,79 es la firma de un modelo
equivocado, no de un mal tuning. En el control sintético de este informe el brazo
defectuoso reproduce exactamente eso (`log10 q = +4,991`, margen 0,009 décadas),
y el corregido queda en el interior (`−0,483`, margen 5,483 décadas).

---

## 3. Archivos y líneas modificadas

Todo bajo `C:/Github/Tesis/src/interfaces/python/geophone_scope/`.

| Archivo | Cambio |
|---|---|
| `kalman_deconv/plant.py` | `_integrator_chain()` → **`_magnitude_change()`**: devuelve `n` ceros en el origen en vez de `n` polos. Nuevo `_DERIVATIVE_ORDER = {displacement:0, velocity:1, acceleration:2}`. En `compose_plant_zpk()` el exponente sale de `spec.geophone.form − spec.estimate`, no de un supuesto fijo. Se agregó fallo explícito para planta impropia y para cascadas sin sensor. Docstrings de módulo y de `geophone_zpk` reescritos (distinción `form` vs `estimate`) |
| `kalman_deconv/models.py` | Comentario de `PlantSpec.estimate` reescrito: decía «agrega un integrador al modelo de entrada» y ahora dice `H_v = s·H_a`, con la advertencia de re-verificar observabilidad |
| `kalman_deconv/report_vs_apparent_masks.py` | Deja de duplicar el primer Kalman y lo importa de `report_velocity_fix`. **SOS posterior pasa a pasa-banda 1–80 Hz** (antes pasa-bajos 0–80, que dejaba pasar DC). Nuevas `compute_masks()`, `assert_reference_is_overlay_only()`, `_fraction_below()`, `_normalize_columns()`, `_sample_on_reference()`, `_masw_comparison()`, `_image_correlation()`. Brazo de comparación `Amedida+SOS` y brazo legacy pasa-bajos agregados a las métricas. Página 5 nueva en el PDF |
| `kalman_deconv/report_velocity_fix.py` | **Nuevo.** Sintético con verdad conocida + brazo defectuoso de control, barrido de sensibilidad de `q`, corrida real de 21 canales con `q` por ML **canal por canal** y NIS descompuesto por ventana, métricas espectrales por banda |
| `test_kalman_plant_magnitude.py` | **Nuevo.** 14 regresiones físicas |
| `test_kalman_reference_overlay.py` | **Nuevo.** 5 pruebas de que la referencia externa es solo overlay |

**No se tocó** `kf.py`, `discretize.py`, `reduce.py`, `library.py`,
`build_catalog.py`, los catálogos JSON ni `cli.py`. La lectura de `û` en
`report_*` (`filtered_state[:, plant_order:] @ input_model.C.T`) sigue siendo
válida sin cambios: `û` **es** la magnitud pedida.

---

## 4. Diseño del primer Kalman y unidades

```
u_k = v_ground   [m/s]        entrada desconocida a estimar
y_k = volts a la entrada del ADC  [V]
x_k = [x_planta ; x_entrada]  estados adimensionalizados por el escalado modal
```

- **Planta:** `sm24_nominal` × `lp_pga_medido`, `estimate="velocity"`.
  zpk compuesto **10 ceros / 13 polos**, grado relativo 3. Tras
  `prepare_plant` (poda de 2 cuasi-cancelaciones + residualización del modo de
  2728 Hz) queda en 7 estados.
- **Modelo de entrada:** `leaky_rw`, `leak_hz = 0,7`, discretizado por Van Loan.
- **Aumentado:** `A = [[Ad, Bd·Cu],[0, Au]]`, `C = [Cd, Dd·Cu]`.
- **`R`:** varianza de la ventana pre-arribo (`t < −0,05 s`) de cada canal.
- **`q`:** máxima verosimilitud de las innovaciones, **por canal**.
- **`P0`:** covarianza estacionaria (`solve_discrete_lyapunov`), nunca `1e6·I`.
- **Solo `kf_forward`. `rts_backward` NO se ejecuta** en esta rama.

**Diferencia con la decisión #2 del HANDOFF.** Esa decisión dice que `v̂_ground`
se obtiene «con un estado integrador dentro del modelo de entrada». Es una
formulación **distinta y también válida** (dejar la planta en aceleración y poner
el integrador en `A_u`). Lo que el código hacía no era ni una ni la otra: ponía
el integrador **en la planta y al revés**. Acá se implementó la conversión de
magnitud en la planta, que es lo pedido; la decisión #2 quedó reescrita para no
seguir afirmando que estimar velocidad agrega un integrador.

---

## 5. Comandos exactos ejecutados

Intérprete: `C:\Users\elias\AppData\Local\Python\pythoncore-3.14-64\python.exe`
(numpy 2.4.6, scipy 1.17.1, PyMuPDF 1.28.0). ⚠ **El `.venv` que menciona el
HANDOFF §2 no existe en esta máquina**; se usó el Python del sistema.

⚠ `REFERENCIAS_KALMAN.md` vive en `geophone_scope/`, **no** en
`geophone_scope/kalman_deconv/` como decía el pedido.

```bash
cd C:/Github/Tesis/src/interfaces/python

# regresiones nuevas
python -m unittest geophone_scope.test_kalman_plant_magnitude
python -m unittest geophone_scope.test_kalman_reference_overlay
python -m unittest geophone_scope.test_masw_ridge_kalman

# gates preexistentes (2 acondicionadores x 3 fs)
python -m geophone_scope.kalman_deconv.cli verify-model --cond comp_nominal  --fs 1020 --report
python -m geophone_scope.kalman_deconv.cli verify-model --cond comp_nominal  --fs 2604 --report
python -m geophone_scope.kalman_deconv.cli verify-model --cond comp_nominal  --fs 2929 --report
python -m geophone_scope.kalman_deconv.cli verify-model --cond lp_pga_medido --fs 1020 --report
python -m geophone_scope.kalman_deconv.cli verify-model --cond lp_pga_medido --fs 2604 --report
python -m geophone_scope.kalman_deconv.cli verify-model --cond lp_pga_medido --fs 2929 --report
python -m geophone_scope.kalman_deconv.cli validate-external

# TEST 1 y TEST 2 rehechos sobre la planta de VELOCIDAD
python -m geophone_scope.kalman_deconv.cli markov --cond lp_pga_medido --estimate velocity
python -m geophone_scope.kalman_deconv.cli check-obsv --cond lp_pga_medido --estimate velocity \
    --input-model leaky_rw   --fs 1020
python -m geophone_scope.kalman_deconv.cli check-obsv --cond lp_pga_medido --estimate velocity \
    --input-model random_walk --fs 1020

# sintético + 21 canales reales + comparación espectral
python -m geophone_scope.kalman_deconv.report_velocity_fix

# MASW, máscaras y PDF
python -m geophone_scope.kalman_deconv.report_vs_apparent_masks
```

---

## 6. Salidas de las pruebas

### 6.1 Regresiones físicas nuevas — 14/14

```
python -m unittest geophone_scope.test_kalman_plant_magnitude
..............
Ran 14 tests in 1.855s
OK
```

Cubren, para `comp_nominal` y `lp_pga_medido` sobre 0,01–1000 Hz (400 puntos
logarítmicos):

- `H_velocity(jω)/H_acceleration(jω) = jω` — error relativo máximo **< 1e−12**;
- `H_displacement(jω)/H_acceleration(jω) = (jω)²` — ídem;
- el cociente es un **derivador**: fase **+90,000°** exactos y pendiente
  **+20,000000 dB/década** (un integrador daría −90° y −20 dB/déc);
- conteo de ceros/polos y grado relativo absolutos de la campaña;
- los ceros extra están **exactamente** en el origen;
- caso bipropio (`comp_nominal` + displacement) con `D ≠ 0` y finito;
- combinación impropia que debe fallar (§6.2);
- realización modal y planta post-`prepare_plant` a las 3 fs;
- ausencia de NaN/Inf en todas las matrices.

Conteos verificados:

| Acondicionador | estimate | ceros | polos | grado rel. |
|---|---|---:|---:|---:|
| `comp_nominal` | acceleration | 3 | 5 | 2 |
| `comp_nominal` | velocity | 4 | 5 | 1 |
| `comp_nominal` | displacement | 5 | 5 | 0 (bipropio) |
| `lp_pga_medido` | acceleration | 9 | 13 | 4 |
| `lp_pga_medido` | velocity | 10 | 13 | 3 |
| `lp_pga_medido` | displacement | 11 | 13 | 2 |

### 6.2 Combinaciones impropias — documentadas

La **única** combinación impropia del catálogo es **geófono solo (sin
acondicionador) + `displacement`**: 3 ceros sobre 2 polos. `compose_plant_zpk`
falla explícitamente con `ValueError: planta impropia: 3 ceros / 2 polos …`.
Geófono solo + `velocity` da 2/2, que es **bipropio y válido**. Una cascada sin
ningún sensor con `estimate ≠ acceleration` también falla explícitamente, porque
su entrada no es movimiento del suelo.

**Limitación numérica documentada** (no es impropiedad): `lp_pga_medido` +
`displacement` es la única combinación cuya **realización modal** se degrada —
0,297 dB dentro de la banda de ajuste y hasta 5,70 dB a 0,01 Hz, década y media
por debajo del límite de validez del modelo (0,211 Hz). La identidad sobre el zpk
sigue exacta a 1e−12; lo que se degrada es el ajuste de `C` por mínimos cuadrados
con doble derivación y cinco décadas de dinámica. La campaña usa `velocity`, que
reproduce el zpk a **0,00000 dB** en todo el rango, igual que `acceleration`.

### 6.3 Gates preexistentes — sin regresión

Las 6 combinaciones (2 acondicionadores × 3 fs) siguen en **TODO PASA**, con los
mismos números que la bitácora de S1:

```
comp_nominal  fs=1020/2604/2929 -> TODO PASA
lp_pga_medido fs=1020/2604/2929 -> TODO PASA

PASS modal vs zpk          1.000-150.0 Hz  mag max 0.0000 dB  fase max 0.000 deg
PASS podado vs original   10.000-50.0  Hz  mag max 0.0001 dB  fase max 0.002 deg
PASS reducido vs original  1.000-150.0 Hz  mag max 0.0035 dB  fase max 0.056 deg
PASS GEO_LP vs explicito   1.000-150.0 Hz  mag max 0.0000 dB  [offset +5.2655 dB]

validate-external -> VALIDACION EXTERNA OK
  planitud 1-100 Hz <= 1,5 dB : OK
  corte a 0.500 Hz +-15%      : OK

python -m unittest geophone_scope.test_masw_ridge_kalman  ->  Ran 3 tests, OK
```

### 6.3b Barrido de renombrado y smoke-import

Para cerrar el requisito de que no quede ninguna afirmación de que estimar
velocidad agrega un integrador:

```bash
grep -rn -i "integrador\|integrator" geophone_scope --include=*.py --include=*.md
```

Quedan 6 coincidencias, **todas deliberadas y todas describiendo el bug ya
corregido** (2 en `HANDOFF_KALMAN.md` marcando la corrección, 1 en `models.py`
que dice explícitamente «NO agrega un integrador», 1 en `report_velocity_fix.py`
que documenta el brazo de control defectuoso, 2 en el test que contrasta
derivador vs integrador). **Ninguna afirma la premisa vieja.**

Además se hizo smoke-import de los cuatro módulos de reporte que **no** cubre
ninguna prueba automática, porque el renombrado podría haberlos roto en silencio:

```
report_s2                    OK
report_descending_masks      OK
report_real_rts_picking      OK
report_real_pdf              OK
```

Y la higiene numérica de la fila 1.13 sigue en cero **llamadas** a `np.roots` /
`tf2ss` en `plant.py`, `reduce.py` y `cli.py` (las 3 coincidencias textuales en
`plant.py` son docstrings que explican por qué no se usan).

### 6.4 TEST 1 — parámetros de Markov, rehecho en velocidad

```
comp_nominal   acceleration : grado z/p = 2 | detectado / retardo L = 2 | PASS
comp_nominal   velocity     : grado z/p = 1 | detectado / retardo L = 1 | PASS
lp_pga_medido  acceleration : grado z/p = 4 | detectado / retardo L = 4 | PASS
lp_pga_medido  velocity     : grado z/p = 3 | detectado / retardo L = 3 | PASS
```

Es **confirmación independiente del signo del cambio**: el grado relativo baja en
uno. Un polo en el origen lo habría **subido** a 5 y 3.

### 6.5 TEST 2 — observabilidad, NO heredada del caso aceleración

Rehecho porque la planta de velocidad tiene un **doble** cero en el origen.

```
                                       PBH en z=1        sigma_min
comp_nominal   acceleration random_walk  4/5  NO OBSERVABLE
comp_nominal   velocity     random_walk  4/5  NO OBSERVABLE
lp_pga_medido  acceleration random_walk  7/8  NO OBSERVABLE
lp_pga_medido  velocity     random_walk  7/8  NO OBSERVABLE
comp_nominal   acceleration leaky_rw     5/5  OBSERVABLE
comp_nominal   velocity     leaky_rw     5/5  OBSERVABLE
lp_pga_medido  acceleration leaky_rw     8/8  OBSERVABLE
lp_pga_medido  velocity     leaky_rw     8/8  OBSERVABLE
```

Margen del PBH equilibrado (σ mínimo), que es lo que **no** se puede heredar:

| Acondicionador | estimate | fs=1020 | fs=2604 |
|---|---|---:|---:|
| `comp_nominal` | acceleration | 5,563e−01 | 2,990e−01 |
| `comp_nominal` | velocity | 1,269e−03 | 4,977e−04 |
| `lp_pga_medido` | acceleration | 4,838e−02 | **2,111e−02** |
| `lp_pga_medido` | velocity | 7,041e−05 | **4,024e−05** |

El valor 2,110783e−02 **reproduce exactamente** el de la bitácora de S2, lo que
confirma que el camino de aceleración quedó intacto.

> **Hallazgo estructural nuevo.** Estimar velocidad sigue siendo observable con
> `leaky_rw`, pero el margen en DC cae **~500×**, consistentemente en los dos
> acondicionadores y las dos fs. No es un detalle: es la causa medible de la
> deriva sub-1 Hz de §8 y la razón por la que el remedio del §7 de la
> Obstrucción 2 (declarar un pasa-altos) pasa de recomendable a necesario.

### 6.6 Referencia externa = solo overlay — 5/5

```
python -m unittest geophone_scope.test_kalman_reference_overlay -v
test_q_is_reproducible_and_reference_free ... ok
test_computation_signatures_have_no_reference_parameter ... ok
test_masw_and_masks_are_bit_identical_across_references ... ok
test_runtime_assertion_accepts_the_real_masks ... ok
test_runtime_assertion_detects_a_tampered_mask ... ok
Ran 5 tests in 18.470s
OK
```

Dos mitades: **estructural** (ninguna función del camino de cálculo —`fit_q_scale`,
`phase_shift_dispersion_image`, `track_dispersion_ridge`, `compute_masks`— tiene
un parámetro por donde entre la referencia) y **numérica** (la cadena corre tres
veces sobre el mismo gather sintético con la referencia real, con una absurda de
250–300 m/s, y **sin ninguna**; se exige igualdad **bit a bit** de la imagen MASW
y de las cuatro máscaras). Incluye **control negativo**: si se altera un solo
píxel de la máscara, la aserción falla.

Además, `report_vs_apparent_masks.generate()` corre en cada ejecución
`assert_reference_is_overlay_only()`, que recalcula la máscara conjunta sin la
referencia y exige igualdad. Queda registrado en el JSON como
`reference_overlay_only_assertion_passed: true`.

---

## 7. Validación sintética — verdad conocida de `v_ground`

Verdad: `v_ground(t) = Ricker 25 Hz + 0,35·Ricker 16 Hz`, fs 1020 Hz, 2 s,
SNR 20 dB, semilla 2909. La medición se sintetiza **siempre** con la planta de
velocidad correcta; lo único que cambia entre brazos es **con qué modelo se
estima**. Solo `kf_forward`.

| Brazo | RMSE rel. alineado | Razón de amplitud | Error de fase 10–50 Hz | lag |
|---|---:|---:|---:|---:|
| **Kalman velocidad corregido** | **0,7510** | **0,8886** | **−1,390°** | +1 |
| Kalman velocidad defectuoso (`H_a/s`) | 4629,15 | 4628,27 | +176,006° | +21 |

Contenido espectral (fracción de PSD normalizada a 1–200 Hz):

| Brazo | 1–10 Hz | 10–50 Hz | 50–80 Hz | 80–200 Hz | Centroide |
|---|---:|---:|---:|---:|---:|
| verdad `v_ground` | 0,020875 | 0,960987 | 0,005139 | 0,000000 | 25,760 Hz |
| **Kalman corregido** | 0,046449 | 0,934418 | 0,005423 | 0,001070 | 24,740 Hz |
| Kalman defectuoso | 0,004040 | 0,987921 | 0,001223 | 0,000079 | 23,059 Hz |
| medición (volts) | 0,025036 | 0,939205 | 0,004426 | 0,001565 | 23,666 Hz |

**Lectura.** El corregido reproduce la forma espectral de la verdad (50–80 Hz:
0,00542 estimado contra 0,00514 verdadero) con amplitud dentro del 11 % y fase
dentro de 1,4°. El defectuoso está tres órdenes y medio fuera en amplitud y con
la fase prácticamente invertida.

**Honestidad sobre el RMSE.** 0,751 es alto porque es **solo filtrado hacia
adelante**: el banco de S2 mostró que el KF solo da 27 % y el RTS 7,9 % en el
caso de aceleración. Acá el RTS está **excluido por consigna**, y además la
planta de velocidad tiene 500× menos margen de observabilidad en DC (§6.5). La
amplitud (0,889) y la fase (−1,39°) muestran que la **forma de onda** se recupera
bien; el RMSE está dominado por contenido de banda ancha no correlacionado.

Diagnósticos numéricos:

```
corregido : min eig(P) = 5,881e-14 > 0 ; P0 = stationary_lyapunov ; salida finita
            NIS sum = 1718,4  con IC95 = [1916,7 , 2167,1]  (dof 2040) -> ratio 0,84
defectuoso: min eig(P) = 1,098e-07     ; NIS sum = 134094,6            -> ratio 65,7
```

### 7.1 Q/R: método, límites, resultado y sensibilidad

- **Método:** `scipy.optimize.minimize_scalar`, `method="bounded"`, sobre
  `log10(q_scale)`, minimizando el negativo del log-verosímil de las
  innovaciones `0,5·Σ[log(2πS) + ν²/S]`. `maxiter=24`, `xatol=0,02`.
- **Límites de búsqueda:** `log10 q ∈ [−8, +5]`.
- **`R`** no se ajusta: sale de la varianza de la ventana pre-arribo. Se
  documenta como **cota superior** (la pre-arribo puede tener cola del disparo
  anterior — HANDOFF §5.6).

```
Barrido explícito de NLL(log10 q), 27 puntos sobre todo [−8, +5]:
  óptimo interior = True        unimodal = True (0 cambios de signo)
  argmin de la grilla = -0,500  vs optimizador = -0,483   (coinciden)

  corregido  : log10 q = -0,483   margen al borde = 5,483 décadas   pegado = False
  defectuoso : log10 q = +4,991   margen al borde = 0,009 décadas   pegado = TRUE
```

El barrido responde la pregunta de sensibilidad al punto inicial y al rango: la
NLL es **unimodal sobre 13 décadas**, con el mínimo en el interior, así que el
resultado no depende de dónde arranque el optimizador. El brazo defectuoso, en
cambio, **se raja contra la cota superior** — y reproduce el `log10 q = 4,99` de
la corrida real defectuosa.

---

## 8. Corrida real — 21 trazas, Canchita grupo 1

`data/processed` vía `reports/real_canchita_group1/real_gathers_and_dispersion.npz`.
21 offsets de 10 a 50 m cada 2 m, fs **1020 Hz**, ventana −0,5 a 3,0 s.
Solo `kf_forward`; **sin RTS**.

### 8.1 Estabilidad numérica

```
canales finitos                 21/21
min eig(P) global               9,999e-15  > 0
P0                              stationary_lyapunov en los 21 canales
q por canal (ML)                mediana log10 q = -4,091
                                dispersión = 1,395 décadas
                                pegados al borde = 0/21
```

Ningún NaN, ningún Inf, ninguna excepción. `q` varía monótonamente con el offset
(−3,33 a 10 m → −4,51 a 50 m), que es lo esperable: menos energía lejos de la
fuente.

### 8.2 NIS — el hallazgo, y por qué el agregado falla

```
NIS medio (21 canales)   0,5279
  ventana pre-arribo     0,0053
  ventana de evento      1,4580
  cola                   0,4040
canales dentro del IC95 de chi-cuadrado   0/21
```

**0/21 pasan el IC estricto, y la descomposición dice por qué.** El NIS de la
ventana pre-arribo es ~0,005, o sea el filtro predice una covarianza de
innovación **~200× mayor** que las innovaciones reales; en la ventana del evento
sube a ~1,46. **Un prior estacionario (`leaky_rw` con un solo `q`) no puede
describir a la vez una ventana silenciosa y un arribo impulsivo**, y la máxima
verosimilitud, que está dominada por el evento, deja el filtro
sobre-conservador en el resto del registro.

Esto es diagnóstico de **mismatch de modelo de entrada**, no de la corrección de
la planta: el ajuste por canal ya mejoró el agregado respecto del atajo anterior
(que escalaba el `q` de un canal de referencia por el cociente de varianzas y
dejaba el NIS medio entre 0,09 y 6,05). Queda como falla abierta en §11.

Comparación con la corrida defectuosa: NIS medio **3,79 → 0,53**.

### 8.3 Comparación espectral de los tres brazos

Mediana sobre los 21 canales. Fracciones normalizadas a 1–200 Hz.

| Brazo | 1–10 Hz | 10–50 Hz | 50–80 Hz | 80–200 Hz | Centroide | Coherencia 10–50 Hz |
|---|---:|---:|---:|---:|---:|---:|
| `Amedida + SOS previo` | 0,007392 | 0,970589 | 0,001365 | 0,000012 | 22,072 Hz | 0,7316 |
| `v_ground Kalman 1` | 0,434373 | 0,557020 | 0,000598 | 0,000004 | 10,274 Hz | 0,7300 |
| `v_ground Kalman 1 + SOS posterior` | 0,269873 | 0,723363 | 0,000727 | 0,000001 | 15,433 Hz | 0,7299 |

SNR evento/pre-arribo, en dB de potencia:

| Brazo | 1–10 Hz | 10–50 Hz | 50–80 Hz | 80–200 Hz |
|---|---:|---:|---:|---:|
| `Amedida + SOS previo` | 8,266 | 27,232 | 15,690 | 3,288 |
| `v_ground Kalman 1` | 4,598 | **30,606** | 16,216 | 6,720 |
| `v_ground Kalman 1 + SOS posterior` | 3,998 | **30,607** | 15,268 | 8,084 |

**Contra el baseline defectuoso** (mediana, corrida vieja, pegada acá como pide
el pedido):

| Métrica | Amedida+SOS | Kalman «velocity» **defectuoso** | Kalman velocity **corregido** |
|---|---:|---:|---:|
| Energía normalizada 1–10 Hz | 0,005357 | 0,0000545 | **0,434373** |
| Energía normalizada 10–50 Hz | 0,978998 | 0,955090 | 0,557020 |
| Energía normalizada 50–80 Hz | 0,001296 | **0,043688** | **0,000598** |
| Centroide 1–200 Hz | 21,902 Hz | **29,716 Hz** | **10,274 Hz** |
| SNR evento/prearribo 10–50 Hz | 28,226 dB | 31,464 dB | 30,606 dB |

Y las métricas MASW del mismo baseline defectuoso, que también deben quedar
registradas:

| Métrica MASW | Amedida+SOS | Kalman «velocity» **defectuoso** |
|---|---:|---:|
| Energía MASW mediana sobre la referencia | 0,419671 | 0,416268 |
| Contraste sobre la referencia | 6,764 dB | 6,951 dB |
| Correlación de imágenes 8–30 Hz | — | 0,9755 |
| Máscara conjunta no vacía | ≈ 2–24 Hz | 7,14–24,00 Hz |

⚠ **Esas cuatro filas NO son comparables con las de §9.2.** Se calcularon con las
definiciones ad-hoc de la corrida de codex; las de este informe usan las
definiciones de §9.3 (normalización por columna, fondo restringido a la máscara
física). Por eso el contraste «baja» de 6,76 a 3,51 dB en el **mismo** brazo
`Amedida + SOS`: **no regresó nada**, cambió la definición. Para juzgar la
corrección hay que comparar dentro de §9.2, que usa una sola definición para
todos los brazos. La correlación 0,9755 sí es indicativa: era alta porque el
modelo defectuoso apenas cambiaba la imagen; con la planta corregida y el SOS
pasa-bajos baja a 0,3796, y con el pasa-banda vuelve a 0,9751.

> El criterio del pedido era: *«La transformación de aceleración a velocidad
> debería desplazar peso relativo hacia frecuencias menores. Si vuelve a realzar
> 50–80 Hz, tratarlo como falla.»* **Se cumple**: el centroide baja 11,8 Hz y la
> banda 50–80 Hz cae de 0,0437 a 0,0006, un factor 73.

### 8.4 La deriva sub-1 Hz — métrica que faltaba

Las fracciones de arriba están normalizadas a 1–200 Hz, que es la convención del
proyecto pero **esconde** lo que hay debajo de 1 Hz. Medido contra la energía
total (0 a Nyquist):

| Brazo | frac < 1 Hz | frac < 3 Hz |
|---|---:|---:|
| `Amedida + SOS previo` | 0,000731 | 0,002241 |
| `v_ground Kalman 1` | **0,387029** | **0,895769** |
| `v_ground Kalman 1 + SOS posterior (1–80 Hz)` | 0,164836 | 0,503640 |

**38,7 % de la energía de `v_ground` está por debajo de 1 Hz.** Es exactamente la
Obstrucción 2 del HANDOFF §6 y coincide con el colapso del margen de
observabilidad en DC de §6.5. Físicamente es esperable: el ruido de medición
blanco, integrado, da una PSD ~1/ω² dominada por las frecuencias más bajas.
Se ve a simple vista en la página 2 del PDF (panel central).

### 8.5 Tabla por canal, los 21

`log10 q` y NIS son de la corrida corregida. Las fracciones de banda comparan el
gather defectuoso guardado en `baseline_defectuoso/` contra el corregido.

| Offset [m] | log10 q | NIS medio | 10–50 Hz medida | 10–50 Hz DEFECT. | 10–50 Hz CORREG. | 50–80 Hz DEFECT. | 50–80 Hz CORREG. | <1 Hz CORREG. |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | −3,334 | 0,6718 | 0,9681 | 0,5929 | 0,8802 | 0,390471 | 0,030081 | 0,1772 |
| 12 | −3,287 | 0,7192 | 0,9512 | 0,6749 | 0,6330 | 0,306155 | 0,034437 | 0,3870 |
| 14 | −3,330 | 0,8974 | 0,9081 | 0,5278 | 0,5590 | 0,411599 | 0,056725 | 0,4355 |
| 16 | −3,261 | 0,9246 | 0,9715 | 0,7358 | 0,7681 | 0,234853 | 0,019076 | 0,4046 |
| 18 | −3,651 | 0,6840 | 0,9801 | 0,8249 | 0,7195 | 0,149611 | 0,008634 | 0,3534 |
| 20 | −3,690 | 1,2027 | 0,9744 | 0,7361 | 0,4653 | 0,217775 | 0,006630 | 0,4351 |
| 22 | −3,827 | 0,5007 | 0,9722 | 0,8556 | 0,6127 | 0,097851 | 0,004047 | 0,4506 |
| 24 | −3,873 | 0,4850 | 0,9638 | 0,6997 | 0,7936 | 0,277633 | 0,017021 | 0,2644 |
| 26 | −3,961 | 0,4325 | 0,9833 | 0,9287 | 0,5911 | 0,056358 | 0,001762 | 0,4016 |
| 28 | −3,962 | 0,4013 | 0,9842 | 0,8976 | 0,7037 | 0,066112 | 0,003167 | 0,2535 |
| 30 | −4,162 | 0,4974 | 0,9852 | 0,9693 | 0,5309 | 0,026167 | 0,000598 | 0,3755 |
| 32 | −4,091 | 0,5631 | 0,9733 | 0,9654 | 0,4042 | 0,021770 | 0,000342 | 0,4124 |
| 34 | −4,245 | 0,2780 | 0,9532 | 0,9469 | 0,5570 | 0,042729 | 0,000587 | 0,2812 |
| 36 | −4,210 | 0,8339 | 0,9499 | 0,9724 | 0,4402 | 0,023218 | 0,000229 | 0,4280 |
| 38 | −4,573 | 0,2202 | 0,9451 | 0,9279 | 0,3881 | 0,066117 | 0,000383 | 0,3020 |
| 40 | −4,656 | 0,2746 | 0,9386 | 0,9369 | 0,2508 | 0,055356 | 0,000193 | 0,3739 |
| 42 | −4,447 | 0,3331 | 0,9355 | 0,9546 | 0,4866 | 0,038741 | 0,000288 | 0,3934 |
| 44 | −4,381 | 0,2872 | 0,9652 | 0,9680 | 0,3871 | 0,027750 | 0,000251 | 0,3841 |
| 46 | −4,292 | 0,3108 | 0,9730 | 0,9814 | 0,5871 | 0,016756 | 0,000213 | 0,4317 |
| 48 | −4,517 | 0,3114 | 0,9706 | 0,9607 | 0,3434 | 0,036769 | 0,000269 | 0,3577 |
| 50 | −4,514 | 0,2568 | 0,9870 | 0,9799 | 0,3268 | 0,018565 | 0,000144 | 0,4309 |

El realce espurio de 50–80 Hz del modelo defectuoso es **más fuerte cuanto más
cerca de la fuente** (0,39 a 10 m contra 0,019 a 50 m): donde hay más señal, más
se amplifica la derivada de más. El corregido lo mantiene en ≤ 0,057 en todos los
canales.

---

## 9. MASW y máscaras

El MASW recibe `v_ground` del primer Kalman filtrado por el SOS posterior, para
los 21 offsets. Paleta pedida: azul = energía baja, rojo oscuro = alta. Cuatro
vistas: energía completa, máscara física, ventana del Kalman 2, e intersección.
La curva externa `Vs_ref = cR_ref/0,92` va superpuesta en cian de alto contraste
(con contorno negro) en **las cuatro**, con leyenda en las vistas 1 y 4.
**No se exporta ningún picking nuevo**; sí la envolvente inferior/superior de la
región válida (`vs_apparent_mask_envelope.csv`).

### 9.1 El SOS posterior tuvo que pasar a pasa-banda

La corrida anterior usaba `butter(10, 80 Hz, "lowpass")`, que **deja pasar DC**.
Con la deriva de §8.4 eso destruye la imagen. Medido:

| SOS posterior | Energía sobre ref. | Percentil | Contraste | Err. máx. local (RMSE) | Correlación 8–30 Hz |
|---|---:|---:|---:|---:|---:|
| `Amedida + SOS` (base) | 0,9862 | 99,7 % | 3,51 dB | 7,24 m/s | — |
| pasa-bajos 0–80 (anterior) | 0,6307 | 80,2 % | 1,77 dB | 11,40 m/s | 0,3796 |
| **pasa-banda 1–80 (adoptado)** | **0,9675** | **99,4 %** | **3,55 dB** | **6,93 m/s** | **0,9751** |

La esquina en 1,0 Hz se eligió para no tocar nada de lo que se reporta: la propia
imagen MASW arranca en **1,14 Hz**. Se probaron 0,5 / 1 / 2 / 3 Hz con resultados
equivalentes (correlación 0,93 / 0,975 / 0,980 / 0,985); 1,0 Hz da el mejor error
de máximo local. Los tres brazos quedan en las métricas
(`v_ground_kalman1_mas_sos_pasabajos_0_80_anterior` conserva el caso anterior)
para que el efecto no haya que redescubrirlo.

> **Declaración de desvío.** El pedido fija la cadena como «SOS posterior» sin
> especificar el tipo. Se cambió de pasa-bajos a pasa-banda, con la justificación
> de arriba, y se dejó el brazo anterior medido al lado. También se mantiene el
> **SOS previo**, que contradice el HANDOFF §8 («KF sobre la señal cruda,
> Butterworth después»): es la cadena que pidió el usuario y se respeta, pero
> queda anotada la tensión.

### 9.2 Comparación final contra `Amedida + SOS`

| Métrica | `Amedida + SOS` | `v_ground Kalman 1 + SOS` |
|---|---:|---:|
| Correlación de imágenes MASW 8–30 Hz | — | **0,9751** |
| Energía normalizada sobre la referencia (mediana) | 0,9862 | 0,9675 |
| Percentil sobre la referencia (mediana) | 99,70 % | 99,40 % |
| Contraste referencia/fondo (mediana) | 3,510 dB | **3,551 dB** |
| Error del máximo local dentro de ±20 m/s — RMSE | 7,239 m/s | **6,926 m/s** |
| Error del máximo local — mediana \|error\| | 1,0 m/s | 1,0 m/s |
| Error del máximo local — sesgo | −0,455 m/s | −0,848 m/s |
| Rango con máscara conjunta no vacía | — | **6,86–23,71 Hz** (60/171 filas) |

Fracciones de grilla: física 0,675 · ventana Kalman 2 0,120 · candidatos
energéticos 0,109 · **conjunta 0,033**.

**Lectura.** Empate técnico. El Kalman gana levemente en contraste (+0,04 dB) y
en error de máximo local (−0,31 m/s), y pierde levemente en energía y percentil
sobre la referencia. Nada de eso es una mejora material. La máscara conjunta
quedó en 6,86–23,71 Hz contra 7,14–24,00 Hz de la corrida defectuosa:
esencialmente igual.

### 9.3 Definiciones de las métricas

Para que sean reproducibles sin leer el código:

- **Normalización:** cada columna de frecuencia de la imagen se divide por su
  máximo. Sin eso, la correlación mediría el espectro de la fuente y no la
  calidad de la imagen de dispersión.
- **Energía sobre la referencia:** valor normalizado de la imagen en
  `(f, c_ref(f))`, mediana sobre los 132 puntos de la curva externa.
- **Percentil sobre la referencia:** percentil que ocupa ese valor dentro de su
  propia columna.
- **Contraste:** `10·log10(valor sobre la referencia / mediana del fondo
  físicamente admisible de la misma columna)`.
- **Error de máximo local:** `argmax` de la columna restringido a
  `|c − c_ref(f)| ≤ 20 m/s`, menos `c_ref(f)`.
- **Correlación de imágenes:** Pearson sobre las imágenes normalizadas
  restringidas a 8–30 Hz (la banda de solape de la referencia).
- Todas se calculan **después** de formar las imágenes.

---

## 10. Artefactos generados

Todas las rutas son absolutas desde `C:/Github/Tesis/`.

| Ruta | Qué es |
|---|---|
| `output/pdf/MASW_VS_APARENTE_MASCARAS_KALMAN.pdf` | PDF regenerado, **5 páginas**, inspeccionado página por página |
| `src/interfaces/python/geophone_scope/kalman_deconv/reports/velocity_model_fix_2026-08-17/INFORME_CORRECCION_VELOCIDAD_KALMAN.md` | **Este archivo** |
| `…/velocity_model_fix_2026-08-17/velocity_fix_metrics.json` | Sintético + Q/R + 21 canales, todo con per-canal |
| `…/velocity_model_fix_2026-08-17/kalman1_velocity_gather_corregido.npz` | `v_ground(t,x)` corregido, crudo y post-SOS |
| `…/velocity_model_fix_2026-08-17/tabla_21_canales.md` | La tabla de §8.5 |
| `…/velocity_model_fix_2026-08-17/baseline_defectuoso/` | **Snapshot del estado defectuoso**, tomado antes de tocar nada: PDF, npz, métricas y envolvente |
| `…/real_canchita_group1/vs_apparent_mask_metrics.json` | Métricas MASW/máscaras de la corrida corregida |
| `…/real_canchita_group1/vs_apparent_mask_envelope.csv` | Envolvente inferior/superior de la región válida |
| `…/real_canchita_group1/kalman1_ground_velocity_gather.npz` | Gather que consume el reporte MASW |

Páginas del PDF: 1 portada y encuadre · 2 waterfall de los tres brazos · 3 las
cuatro vistas de máscara con la referencia superpuesta · 4 envolvente admisible ·
5 resumen de las dos etapas + comparación MASW contra la aceleración medida.
Se renderizaron las 5 a PNG con PyMuPDF a 110 dpi y se revisaron: sin texto
cortado, sin solapamientos, sin gráficos ilegibles. En la primera pasada había
una leyenda pisando el rótulo del eje en la página 3 y un párrafo huérfano en una
sexta página; las dos cosas están corregidas.

---

## 10b. Segunda iteración: el prior de entrada `ou_band`

Todo lo de §7–§9 se midió con el prior `leaky_rw` de 0,7 Hz. **El default cambió
a `ou_band` centrado en 10–50 Hz** por el resultado que sigue.

**Motivo.** El modelo directo ya era el más detallado del repo —13 polos que
incluyen los del AFE medido (258, 265,7, 291, 2728 Hz) y el par del geófono en
10 Hz—, así que ahí no quedaba información por agregar. La pieza pobre era el
**modelo de entrada**: `leaky_rw` es un estado y un polo, plano hasta DC, o sea
no le dice al estimador que el suelo no se mueve en continua. Por eso el filtro
ponía 38,7 % de la energía por debajo de 1 Hz (§8.4). `ou_band` es un oscilador
de Ornstein-Uhlenbeck de 2 estados con banda pasante: **codifica la banda útil
como física, no como post-filtrado**.

Barrido exploratorio sobre 3 canales (10 / 30 / 50 m):

| Prior | frac < 1 Hz | frac 10–50 Hz | PBH z=1 |
|---|---|---|---|
| `leaky_rw` 0,7 (anterior) | 0,177 / 0,376 / 0,431 | 0,880 / 0,531 / 0,327 | 8/8, σ 7,04e−5 |
| `leaky_rw` 3 | 0,087 / 0,196 / 0,289 | 0,910 / 0,772 / 0,584 | 8/8, σ 7,08e−5 |
| `leaky_rw` 10 | 0,023 / 0,054 / 0,089 | 0,937 / 0,933 / 0,847 | 8/8, σ 7,08e−5 |
| **`ou_band` 10–50** | **0,004 / 0,008 / 0,013** | **0,952 / 0,982 / 0,970** | **9/9, σ 7,08e−5** |
| `ou_band` 5–60 | 0,005 / 0,010 / 0,016 | 0,951 / 0,979 / 0,963 | 9/9, σ 7,08e−5 |

Dos cosas: con `leaky_rw` la energía en la banda útil **se degrada con el
offset** (0,880 → 0,327 al alejarse de la fuente) y con `ou_band` se mantiene en
0,95–0,98; y la observabilidad no se paga (rango completo, mismo σ mínimo).

**Sintético, verdad conocida:**

| Métrica | `leaky_rw` 0,7 | **`ou_band` 10–50** |
|---|---:|---:|
| RMSE relativo alineado | 0,7510 | **0,2748** |
| Razón de amplitud | 0,8886 | 0,7855 |
| Error de fase 10–50 Hz | −1,390° | **+0,143°** |
| frac 1–10 Hz (verdad: 0,020875) | 0,046449 | **0,022836** |
| frac 10–50 Hz (verdad: 0,960987) | 0,934418 | **0,957598** |
| frac 50–80 Hz (verdad: 0,005139) | 0,005423 | 0,005957 |

RMSE **2,7× mejor**, y queda a la par del banco de S2 para KF-solo en
aceleración (0,272). ⚠ **Control anti-imposición**: la verdad tiene 2,09 % en
1–10 Hz y el estimador recupera **2,28 %**, no cero. El prior acota la deriva
pero no dicta el resultado. La amplitud baja de 0,889 a 0,786: es el
encogimiento esperable de un prior más informativo, y es el precio.

**21 canales reales:**

| Métrica (mediana) | `leaky_rw` 0,7 | **`ou_band` 10–50** | `Amedida + SOS` |
|---|---:|---:|---:|
| frac < 1 Hz del total | 0,387029 | **0,009769** | 0,000731 |
| frac 10–50 Hz | 0,557020 | **0,962887** | 0,970589 |
| Centroide | 10,274 Hz | 22,921 Hz | 22,072 Hz |
| SNR 10–50 Hz | 30,606 dB | 28,985 dB | 27,232 dB |
| Coherencia 10–50 Hz | 0,7300 | 0,7281 | 0,7316 |
| NIS medio | 0,528 | 0,626 | — |
| Canales en IC95 del NIS | 0/21 | 1/21 | — |

`min eig(P)` = 1,0000e−14 > 0, 21/21 finitos, 0/21 pegados al borde.

**MASW:**

| Métrica | `Amedida + SOS` | `leaky_rw` 0,7 | **`ou_band` 10–50** |
|---|---:|---:|---:|
| Correlación de imágenes 8–30 Hz | — | 0,9751 | **0,9841** |
| Energía sobre la referencia | 0,9862 | 0,9675 | **0,9814** |
| Percentil sobre la referencia | 99,70 % | 99,40 % | 99,40 % |
| Contraste referencia/fondo | 3,510 dB | 3,551 dB | 3,434 dB |
| Error del máximo local (RMSE) | 7,239 m/s | 6,926 m/s | **6,913 m/s** |
| **Máscara conjunta no vacía** | — | 6,86–**23,71** Hz (60/171) | **6,86–46,57 Hz (115/171)** |

**El resultado que cambia la conclusión de §1.** Contra la curva de referencia
sigue siendo un empate técnico. Pero la **región admisible casi se duplica**:
115 filas de frecuencia contra 60, llegando a 46,6 Hz en vez de cortarse en
23,7 Hz. Esa es banda utilizable que la deriva se estaba comiendo, y para la
inversión posterior más cobertura en frecuencia es más resolución en
profundidad.

⚠ **Dos límites de esa afirmación.** La referencia hidro solo cubre 8,0–29,8 Hz,
así que **la ampliación por encima de 29,8 Hz no se puede validar contra nada
externo** — se sabe que la máscara conjunta no está vacía ahí, no que la
velocidad que contenga sea correcta. Y el contraste bajó 0,08 dB, que es ruido.

**Estado del SOS posterior pasa-banda 1–80 Hz.** Con `ou_band` la deriva ya es
0,0098 y el pasa-banda deja de ser necesario para que el MASW funcione; se
mantiene igual porque sigue bajando la deriva residual a 0,0037 y no cuesta
nada. Ya no es un parche: es higiene.

---

## 11. Limitaciones, fallas abiertas y decisiones pendientes

**Limitaciones declaradas:**

1. **No hay verdad sincronizada de velocidad de partícula en el repositorio.**
   La amplitud absoluta de `v_ground` en datos reales **no está validada
   metrológicamente** y no se presenta como tal. Lo que se usa es la fase
   relativa entre canales.
2. **La banda 1–10 Hz no es reportable.** La SNR real de campo ahí es ~0 dB
   (HANDOFF §5.5), y el brazo Kalman la **empeora** (8,27 → 4,00 dB): lo que la
   integración agrega en baja frecuencia es mayormente ruido amplificado, no
   señal.
3. **Solo una fs.** La corrida real es a 1020 Hz. El criterio 4.1 de
   `TAREAS_KALMAN.md` pide dos fs; sigue sin cumplirse.
4. **`P` es optimista.** Bajo error de modelado deja de ser la covarianza real
   (Maes et al. 2016). La barra de error defendible se calcula por la Ec. (70),
   que **todavía no está implementada** (S3).
5. **Sin RTS y sin picking nuevo**, por consigna.

**Fallas abiertas:**

6. **NIS: 0/21 canales dentro del IC95.** Causa identificada y medida (§8.2): el
   prior `leaky_rw` es estacionario y el registro no lo es. Remedios candidatos,
   en orden de esfuerzo: `q` variable en el tiempo (dos regímenes,
   pre-arribo/evento), un prior `ou_band` centrado en 10–50 Hz, o un modelo de
   entrada no estacionario. **Ninguno se probó.**
7. **`lp_pga_medido` + `displacement`**: realización modal degradada (§6.2). No
   bloquea nada hoy porque la campaña usa `velocity`.
8. **La suite anti-invención de S3 sigue sin existir.** Este informe muestra un
   caso concreto en el que hacía falta: la deriva sub-1 Hz del §8.4 es
   exactamente lo que el test de «energía por tercio de octava» y el de «entrada
   nula» habrían detectado solos.

**Decisiones pendientes para el usuario:**

9. **¿Se acepta el SOS posterior pasa-banda 1–80 Hz?** Está justificado y medido
   (§9.1), pero es un cambio a la cadena especificada.
10. **¿Vale la pena seguir con el brazo de velocidad para MASW?** Medido: es
    equivalente a `Amedida + SOS`, no mejor. La alternativa —que el HANDOFF §4
    decisión 2 ya contemplaba— es hacer el MASW **en aceleración**, dado que el
    *phase-shift* usa fase y basta con que todos los canales usen la misma
    magnitud.
11. **`leak_hz` del `leaky_rw`** sigue en 0,7 Hz. Subirlo es la palanca del lado
    Kalman para acotar la deriva dentro del modelo en vez de por post-filtrado.
    **No se barrió**; es el siguiente experimento natural.

---

## 12. Estado del árbol de trabajo

**No se hizo ningún commit.** Tampoco se creó ni cambió de rama. El submódulo
`src/interfaces/python` sigue en `main`; el superproyecto en `cambios-hardware`.
Se preservaron todos los cambios locales previos de codex (nada revertido).

`git status --short` en `src/interfaces/python` al cierre:

```
 M geophone_scope/HANDOFF_KALMAN.md
 M geophone_scope/TAREAS_KALMAN.md
 M geophone_scope/kalman_deconv/__init__.py
 M geophone_scope/kalman_deconv/cli.py
 M geophone_scope/kalman_deconv/discretize.py
 M geophone_scope/kalman_deconv/models.py          <- esta sesión
 M geophone_scope/kalman_deconv/plant.py           <- esta sesión
 M geophone_scope/kalman_deconv/synthetic.py
?? geophone_scope/PEDIDO_CLAUDE_CORREGIR_VELOCIDAD_KALMAN.md
?? geophone_scope/kalman_deconv/report_descending_masks.py
?? geophone_scope/kalman_deconv/report_real_pdf.py
?? geophone_scope/kalman_deconv/report_real_rts_picking.py
?? geophone_scope/kalman_deconv/report_s2.py
?? geophone_scope/kalman_deconv/report_velocity_fix.py       <- esta sesión
?? geophone_scope/kalman_deconv/report_vs_apparent_masks.py  <- modificado
?? geophone_scope/kalman_deconv/reports/
?? geophone_scope/masw_ridge_kalman.py
?? geophone_scope/test_kalman_plant_magnitude.py             <- esta sesión
?? geophone_scope/test_kalman_reference_overlay.py           <- esta sesión
?? geophone_scope/test_masw_ridge_kalman.py
```

Nota: `report_vs_apparent_masks.py` figura como `??` porque nunca fue commiteado
por codex; esta sesión lo modificó, no lo creó.
