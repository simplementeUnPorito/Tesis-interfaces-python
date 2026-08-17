# HANDOFF — Deconvolución Kalman+RTS y picking Kalman para MASW

**Este es el documento que se lee primero al retomar.** Los otros dos:

- **`TAREAS_KALMAN.md`** — el checklist vivo: qué función falta, con qué comando
  se verifica y cuál es el criterio numérico.
- **`REFERENCIAS_KALMAN.md`** — bibliografía verificada, con el estado
  VERIFICADO / PROBABLE / NO VERIFICADO de cada cita.

Escrito el 2026-08-17. Pensado para que cualquier sesión o agente futuro
—incluido codex— pueda continuar sin re-derivar nada.

---

## 1. Qué es y qué NO es

**Qué es.** Dos estimadores bayesianos, independientes entre sí, que se agregan
al pipeline de `src/interfaces/python`:

1. **Deconvolución temporal (KF + RTS).** Usa el modelo físico
   *geófono × acondicionador* para estimar el movimiento del suelo a partir de
   la señal del ADC. Estado aumentado (*joint input-state estimation*) hacia
   adelante, y suavizado de Rauch–Tung–Striebel hacia atrás aprovechando que el
   procesamiento es offline.
2. **Picking de dispersión (segundo Kalman).** Recorre los **bins de
   frecuencia**, no el tiempo, siguiendo la cresta modal sobre `P(f,c)` con
   `R(f)` dependiente de la calidad de cada candidato.

**Qué NO es:**

- **No reemplaza al Butterworth.** El filtro del pipeline sigue siendo
  Butterworth SOS + `sosfiltfilt` (fase cero), que es mejor que un FIR para esto
  y se mantiene por decisión explícita.
- **No toca Qt ni FastAPI en las primeras fases.** El módulo es de funciones
  puras, verificable por CLI. La integración web viene después (S5).
- **No promete señal donde no la hay.** La banda útil real de las campañas
  existentes es **10–50 Hz**; bajo 10 Hz la SNR es ~0 dB. Cualquier afirmación
  sobre 1–10 Hz con datos reales está limitada por el piso de ruido. El banco
  sintético sí puede barrer 1–100 Hz.
- **No puede crear información.** La mejora tiene que venir de combinar la
  medición con el modelo físico, y hay una batería de tests que lo verifica
  (§6). Si el filtro fabrica contenido de baja frecuencia, los tests lo detectan
  y eso es un resultado, no un bug a esconder.

---

## 2. Cómo probar

Todo por línea de comandos, sin GUI:

```bash
cd C:/Github/Tesis/src/interfaces/python

# catalogo de modelos
python -m geophone_scope.kalman_deconv.cli models
python -m geophone_scope.kalman_deconv.cli models lp_pga_medido

# modelo: realizacion, poda, residualizacion, verificacion de la FRF.
# Correr las 6 combinaciones: {comp_nominal, lp_pga_medido} x {1020, 2604, 2929}
python -m geophone_scope.kalman_deconv.cli verify-model --cond lp_pga_medido --fs 2604
python -m geophone_scope.kalman_deconv.cli verify-model --cond comp_nominal --fs 1020

# validacion externa del constructor de modelos contra Ma et al. 2023
python -m geophone_scope.kalman_deconv.cli validate-external

# regenerar el catalogo desde el informe (solo si cambia la fuente)
python -m geophone_scope.kalman_deconv.build_catalog

# observabilidad: el primero debe FALLAR, el segundo debe PASAR
python -m geophone_scope.kalman_deconv.cli check-obsv --input-model random_walk
python -m geophone_scope.kalman_deconv.cli check-obsv --input-model leaky_rw

# banco sintético
python -m geophone_scope.kalman_deconv.cli bench --case ricker25 --fs 2604 --snr 20

# batería anti-invención
python -m geophone_scope.kalman_deconv.cli no-invent --fs 2604

# comparación de brazos sobre una captura real
python -m geophone_scope.kalman_deconv.cli compare --capture <ruta>
```

Para la parte web, cuando llegue S5, el gate existente es
`python server/smoke_test.py` contra `data/raw`.

Entorno: `.venv/Scripts/python.exe` del repo. Para probar GUI sin pantalla,
`QT_QPA_PLATFORM=offscreen`.

---

## 3. Estado de las sesiones

| # | Sesión | Estado | Fecha | Commit |
|---|---|---|---|---|
| S0 | Investigación, verificación del modelo, bibliografía, documentos | ✅ hecho | 2026-08-17 | sin commitear |
| S1 | `models.py`, `library.py`, `plant.py`, `reduce.py`, catálogos JSON | ✅ 13/13 | 2026-08-17 | sin commitear |
| S2 | `discretize.py`, `kf.py`, `synthetic.py` — KF+RTS y recuperación sintética | ⬜ | | |
| S3 | `estimate.py`, `metrics.py` — Q/R, anti-invención, comparación de brazos | ⬜ | | |
| S4 | Capturas reales de `Canchita_grupo1_procesado`, reporte | ⬜ | | |
| S5 | Integración web: tab Filtros | ⬜ | | |
| S6 | `masw_ridge_kalman.py` — tracker de cresta, PWS, ridge/Hessiano | ⬜ | | |
| S6b | Benchmarks obligatorios: HLRT, sparse-L1, E-DBSCAN | ⬜ | | |
| S7 | Inversión y comparación contra `Moldeo Hidro` | ⬜ | | |

---

## 4. Decisiones cerradas — no reabrir sin motivo nuevo

1. **El geófono se modela en la forma de ACELERACIÓN**:
   `H_a(s) = −G·s/(s² + 2ζ₀ω₀s + ω₀²)`, **entrada = aceleración del suelo**.
   Es la de Ma et al. (2023) y la misma que usa el `informe_identificacion.txt`.
   La forma en `s²` es la de entrada en velocidad y **no se usa**.
2. **La magnitud nativa estimada es aceleración.** `v̂_ground` se obtiene con un
   **estado integrador dentro del modelo de entrada**, no integrando después:
   así la incertidumbre sale de la covarianza en vez de quedar escondida.
   Para MASW también sirve quedarse en aceleración (el *phase-shift* usa fase),
   siempre que **todos los canales usen la misma magnitud**.
3. **Modelo de entrada por defecto: `leaky_rw`**, no *random walk* puro.
   Razón en §5.2 — no es un detalle de tuning, es lo que evita drift inventado.
4. **KF sobre la señal cruda; Butterworth después, solo para visualizar.**
   El notch armónico va **antes**. Conmutable, con *warning* si se elige `pre`.
   Razones en §5.4.
5. **Realización modal + residualización.** Nunca `tf2ss` sobre los polinomios
   (rango dinámico de coeficientes ~1e25), y nunca truncamiento balanceado
   (los gramianos sobre 5 décadas son ellos mismos mal condicionados).
6. **Biblioteca de modelos seleccionable**, con GEO y CONDITIONER independientes,
   cargables y guardables. No es un lujo: los modelos nominal y medido plantean
   problemas distintos y hay que poder correr los dos (§5.5).
7. **La tanda del 20/07/2026 está DESCARTADA.** Solo se usa la calibrada del
   21/07. El `ζ = 83,661` que aparece en documentos viejos es
   `zeta_realizada_componentes` de la tanda descartada, **no un objetivo de
   diseño**.
8. **El Butterworth SOS `sosfiltfilt` se mantiene**; no se cambia por un FIR.
9. **Todo lo que sea opción de UI nace como campo de dataclass**, no como `if`
   hardcodeado, para que la integración web sea solo exponer controles.

---

## 5. Hallazgos clave

Todos verificados contra fuente primaria el 2026-08-17. **No re-derivar.**

### 5.1 Correcciones a premisas que circulaban

| Premisa | Realidad verificada |
|---|---|
| Geófono de 4,5 Hz | **SM-24 de 10 Hz.** G₀ = 28,8 V·s/m, ζ₀ = 0,25 a circuito abierto, 0,60 con shunt de 1339 Ω, bobina 375 Ω, masa 11 g. El repo desmiente el 4,5 Hz en dos lugares |
| ζ = 83,661 es un objetivo | Es de la tanda **descartada**. La calibrada da 0,25 |
| El pipeline usa FIR | Usa **Butterworth SOS + `sosfiltfilt`**. El FIR de `signal_proc.py` es solo para adquisición en tiempo real |
| La app es PyQt | La activa es la **web FastAPI + JS vanilla** en `server/`. La PyQt (`field_review_app.py`) está siendo portada |
| El AFE medido «no tiene muesca» | **Impreciso.** La muesca existe, pero es de **2,31 dB** en vez de los **26,13 dB** del nominal ideal. Ver §5.3 |

### 5.2 El modelo compuesto: nominal vs medido

El numerador del compensador **nominal** está diseñado para cancelar los polos
del geófono. **Pero la cancelación no es exacta** (verificado en S1): el
compensador está sintonizado a ω₀ = 64,117378 rad/s = **10,2046 Hz** y el SM-24
nominal es de **10,0 Hz**. Queda un dipolo residual del 2 %:

```
comp_nominal x SM-24  ->  3 ceros / 5 polos  ->  grado relativo 2
  polos: -0,0342 (5,44 mHz) | -15,708 +-60,837j (10 Hz) | -2469 (393 Hz) | -120207 (19,1 kHz)
  ceros: 0 | -16,029 +-62,081j (10,2046 Hz)
  dipolo residual de la desintonia: delta relativo 2,05e-2 -> 0,26 dB y 4,2 grados
  respuesta: ripple ~0,6 dB entre 0,1 y 100 Hz
```

La desintonía está dentro de la tolerancia de ±2,5 % del SM-24, así que es
esperable — pero **no es despreciable y no hay que podarla**: es física, no
artefacto de ajuste. Por eso `cancel_tol_ratio` vale 5e−3 y no 5e−2.

Con el **medido** (`LP_PGA`, 11 polos / 8 ceros) la cascada es 9 ceros / 13
polos, **grado relativo 4**, y **mantiene la resonancia del geófono a 10 Hz**.

**Los dos modelos responden preguntas distintas, y ahí está el experimento:**

| | `comp_nominal` | `lp_pga_medido` |
|---|---|---|
| Respuesta en banda | plana | mantiene el pasa-altos del sensor |
| Qué hace el KF+RTS | **denoising óptimo** — el compensador analógico ya hizo el trabajo | **deconvolución real** |
| Rol | hardware ideal / referencia | el problema efectivo con el hardware que existe |

⚠ **Trampa del modelo nominal, para que nadie la lea como bug.**
Deconvolucionar capturas **reales** con `comp_nominal` es un desajuste de modelo
deliberado: el hardware no aplicó esa extensión de banda, así que el KF va a
intentar deshacer algo que nunca ocurrió y **eso fabrica contenido de baja
frecuencia**. La suite anti-invención es justamente el instrumento que
discrimina los dos modelos sobre datos reales.
**Expectativa declarada: sobre capturas reales `comp_nominal` debe FALLAR
blancura y reconstrucción, y `lp_pga_medido` debe pasar. Sobre sintéticos
generados con el nominal, debe pasar. Esa asimetría es la demostración, no un
error a corregir.**

### 5.3 Profundidad de la muesca — confirmación independiente

| Profundidad (1 Hz → 10,2046 Hz) | Valor |
|---|---|
| Compensador **nominal** ideal | **26,13 dB** |
| Compensador **medido** (CH3/CH1, tanda calibrada) | **2,31 dB** |

La diferencia reproduce, por un camino independiente, el hallazgo ya documentado
de que **la muesca quedó 26,33 dB menos profunda que el modelo** (el repo compara
−57,58 dB predichos con el modelo del operacional contra −31,26 dB medidos).

Comando que lo regenera:

```bash
cd C:/Github/Tesis/src/interfaces/python && python -c "
import numpy as np
from scipy import signal
z0=0.25; z1=937.39633; w0=64.117378; Kdc=-3.9705882; tau=1/(2*np.pi*392.975168)
n=[Kdc,2*z0*w0*Kdc,w0**2*Kdc]; d=np.polymul([1,2*z1*w0,w0**2],[tau,1])
cn=[0,-9814.164304,-195155945.6,-1.188922861e+12,-7.472622163e+14,-3.966346735e+15]
cd=[1,27864.2401,1062243197,1.558872813e+13,2.699797681e+16,9.745368195e+14]
w=2*np.pi*np.array([1.0,10.2046])
_,Hn=signal.freqs(n,d,w); _,Hm=signal.freqs(cn,cd,w)
print('nominal %.2f dB   medido %.2f dB'%(20*np.log10(abs(Hn[0]/Hn[1])), 20*np.log10(abs(Hm[0]/Hm[1]))))
"
```

### 5.4 Polos y ceros del AFE medido (`LP_PGA`)

| Polos (rad/s) | f (Hz) | Ceros (rad/s) | f (Hz) |
|---|---|---|---|
| −4661,4 ± 16496j | 2728,3 | −5324,1 | 847,4 |
| −1669,4 | 265,7 | −753,78 | 120,0 |
| −1245,9 ± 1338j | 291,0 | −5,4153 | 0,862 |
| **−14,514 ± 1622,1j** | **258,18** | **−14,757 ± 1621,8j** | **258,12** |
| **−0,11966 ± 1,6305j** | **0,2602** | **−0,11637 ± 1,633j** | **0,2606** |
| −0,0042521 ± 0,30864j | 0,0491 | **+0,060523** | **0,0096 (RHP)** |

Tres cosas que mandan sobre el diseño:

1. **Un cero en el semiplano derecho** en `s = +0,0605 rad/s` (≈0,0096 Hz). El
   inverso causal es inestable → **es el argumento técnico a favor de RTS**.
   Está tres décadas bajo la banda útil y veinte veces bajo el límite de validez
   de la identificación (0,211 Hz): casi seguro artefacto del ajuste — el último
   coeficiente del numerador (−5,19e23) es tres órdenes menor que el anterior.
2. **Dos cuasi-cancelaciones polo-cero**: en 258 Hz (Δ relativo 1,5e−4) y en
   0,26 Hz (2e−3). El orden efectivo baja de 11 a ~7.
3. **El par de 2728 Hz está por encima de Nyquist en TODAS las fs disponibles**
   (Nyquist máx. 1302 Hz a fs = 2604; 510 Hz a fs = 1020). Discretizarlo con ZOH
   lo **aliasa a una frecuencia falsa dentro de banda**: hay que residualizarlo
   **antes** de discretizar, no después.

Comando que lo regenera:

```bash
cd C:/Github/Tesis/src/interfaces/python && python -c "
import numpy as np
num=[0,0,0,5.595521538e+10,3.420527472e+14,3.836878861e+17,9.033098929e+20,5.957043684e+23,3.303624364e+24,2.1384983e+24,8.431407583e+24,-5.18866801e+23]
den=[1,13513.34939,343178285.5,1.343758243e+12,3.189769101e+15,5.122608358e+18,5.987308998e+21,4.316499374e+24,1.085908459e+24,1.195293578e+25,1.979867545e+23,1.098802084e+24]
z=np.roots(num); p=np.roots(den)
print('ceros RHP:',int(np.sum(z.real>0)))
print('rango de polos: %.4g .. %.4g rad/s'%(np.min(np.abs(p)),np.max(np.abs(p))))
"
```

### 5.5 Realidad de banda (verificado por cinco caminos)

| Banda | SNR mediana | Grupos > 10 dB |
|---|---|---|
| 1–2,5 Hz (banda **objetivo** del proyecto) | 0,4 dB | 0 de 16 |
| 5–10 Hz | 1,3 dB | 0 de 16 |
| 10–20 Hz | 15,2 dB | 13 de 16 |
| 20–50 Hz | 25,6 dB | 14 de 16 |

Fuente: `docs/Primera Presentación/latex-historial/secciones/24_banda_util_real.tex`.

### 5.6 Datos disponibles

- **Capturas reales:** `data/processed/Canchita_grupo1_procesado` — 477
  muestras, 21 distancias de 10 a 50 m cada 2 m, fs 1020 y 2929 Hz.
  Geometría: fuente en 0 m, Δx = 2 m, offset mínimo 10 m, apertura L = 40 m.
  El arreglo es **sintetizado** moviendo un solo geófono (un disparo por
  posición), no un arreglo físico simultáneo.
- **Referencia de comparación:** `data/Moldeo Hidro` — **no tiene capturas**,
  son 3 archivos con el *resultado* de un trabajo previo: curva de dispersión
  guiada por hidrogeología (132 puntos, 8,0–29,8 Hz, c 72–102 m/s) y un modelo
  `Vs(z)` de 7 capas por litología. Verificada en `28_sev_guiada.tex`, que
  recomienda adoptarla como curva de trabajo por ser la mejor condicionada.
- ⚠ **No hay registro de ruido de fondo dedicado** en `data/raw` (solo
  Canchiga, Canchita, Canchita_2, Estacionamiento y las tandas de osciloscopio).
  Para el test de entrada nula, el fallback es concatenar ventanas
  **pre-trigger** hasta juntar ≥ 30 s. **No son equivalentes**: la pre-trigger
  puede contener cola del disparo anterior. Documentar cuál se usó.
- **fs:** 2604 Hz es la nativa del firmware vigente; 2929 y 1020 son
  configuraciones históricas que quedaron en los metadatos. **fs siempre es
  parámetro, nunca constante de módulo.**

---

## 6. Las tres obstrucciones, y sus tests

Estas son las razones por las que «KF+RTS» a secas no alcanza y hay que
verificar antes de confiar en un resultado. Respaldo bibliográfico completo en
`REFERENCIAS_KALMAN.md` §2.

### Obstrucción 1 — grado relativo ≥ 2 ⇒ la inversión instantánea falla

Con la forma en aceleración el geófono es 1 cero / 2 polos → grado relativo 1.
En cascada: **2** con `comp_nominal`, **4** con `lp_pga_medido`. En los dos
casos `D = 0` en continuo y `J = 0` bajo ZOH ⇒ la variante *con feedthrough* de
Gillijns & De Moor (2007b) **no aplica**, y la variante sin feedthrough exige
`rank(CB) = 1`, pero `CB = 0`.

**Remedio:** retardo `L ≥ 2` (Maes, Gillijns & Lombaert 2018 — permitir retardo
**relaja** las condiciones de invertibilidad). Como el procesamiento es offline
el retardo es gratis, y el AKF+RTS ya usa toda la ventana.

> **TEST 1:** calcular `CB`, `CAB`, `CA²B`, `CA³B` del modelo discretizado. El
> índice del primer parámetro de Markov no nulo **es** el retardo mínimo `L`.

### Obstrucción 2 — cero en s = 0 ⇒ inobservabilidad en DC ⇒ drift

`H_a` tiene **un** cero en el origen (`q = 1` en la nomenclatura de Maes et al.
2016 §2.3). Bajo ZOH, `exp(0·Δt) = 1` → un cero marginalmente estable en z = 1.
Si se aumenta el estado con *random walk* (polo en z = 1) hay **cancelación
polo-cero y pérdida de observabilidad en DC**: `P` crece sin cota, el estimado
deriva, y el RTS suaviza esa deriva hasta algo que **parece** señal sísmica de
1–2 Hz y no lo es.

**Ningún ajuste de Q elimina ese drift; solo lo esconde.** El geófono
físicamente no mide DC. Los remedios de la literatura son todos la misma idea
—aportar información donde el dato no la tiene— y hay que **declararlos como
supuesto, no presentarlos como recuperación**:

- *dummy-measurements* a nivel de posición (Naets et al. 2015);
- modelo de entrada con el polo **fuera** de z = 1 → es el **`leaky_rw`** que se
  adopta como default (`A_u = exp(−2π·f_leak·dt)`, `f_leak` = 0,7 Hz): plano en
  10–50 Hz, acotado en DC;
- pasa-altos explícito bajo 10 Hz con banda de validez declarada — **benigno
  acá**, porque la banda útil es 10–50 Hz y no se pierde nada de MASW.

`random_walk` puro queda disponible **con warning**, para poder *demostrar* el
drift en el banco.

> **TEST 2 (el más importante):** PBH en z = 1 sobre el par aumentado —
> `rank([A_a − I; C_a])`. Si es menor que n+1, el modo DC es inobservable y el
> drift es matemáticamente inevitable. Complementar con el **espectro de valores
> singulares** del gramiano; **nunca** `matrix_rank` a secas (con 5 décadas de
> dinámica el rango numérico es basura).

### Obstrucción 3 — fase no mínima, por dos fuentes distintas

**(a) El cero RHP del AFE** en `s = +0,0605 rad/s`. En banda es irrelevante,
pero **prohíbe el inverso causal estable**. Esto no es un problema sino **el
argumento a favor del enfoque**: la inversión de un sistema de fase no mínima
admite solución acotada **si y solo si** se permite que sea no causal — la
dinámica interna inestable se resuelve *hacia atrás en el tiempo*. Un suavizador
de intervalo fijo **es** un inversor no causal.

> Frase para defender ante tribunal: *el cero RHP prohíbe el filtro inverso
> causal, y por eso el estimador tiene que ser un suavizador de intervalo, no un
> filtro.*

**(b) Ceros de muestreo.** Åström, Hagander & Sternby (1984): con grado relativo
> 2 y muestreo rápido, **el sistema muestreado es siempre de fase no mínima**.
Con `lp_pga_medido` el grado relativo es 4 → la discretización **crea** ceros
fuera del círculo unidad que no existen en el continuo. Y Maes et al. (2016),
verbatim: *«Unstable zeros lead to large errors on the estimated quantities, due
to the amplification of measurement errors and, therefore, should be avoided»*.

> **TEST 3:** clasificar los ceros de `c2d(sys, 1/fs)` respecto de `|z| = 1`
> para **cada fs (1020, 2604, 2929)** y comparar contra los ceros del continuo,
> para separar los intrínsecos de los de muestreo. **Es plausible que fs = 1020
> se comporte mejor que 2929 para la inversión**, aunque suene contraintuitivo.
> Si es así, cambia la recomendación de adquisición — vale la pena saberlo.

### Sobre SISO

Con 1 entrada y 1 salida la condición `n_salidas ≥ n_entradas` se cumple con
igualdad y el sistema es cuadrado e invertible. Pero no hay redundancia:
**cualquier error de modelado se traduce 1:1 en error de entrada**. Por eso
`kf_forward` acepta `y` de forma `(N,)` o `(N, L)` desde el día uno — L geófonos
compartiendo `u_k` mejoran la observabilidad ~L× sin refactorizar.

---

## 7. La batería anti-invención

Es el requisito central, así que es una suite con umbrales, no una intención.

**Propios:**

1. **LF-null injection** — señal con energía *exactamente* nula bajo 10 Hz
   (síntesis en frecuencia). Criterio: `u_hat` ≥ 20 dB por debajo en 0–10 Hz
   respecto de 10–50 Hz, y **sin pico coherente en 1–2,5 Hz**.
2. **Deconvolución de ruido puro** — la PSD de `u_hat` debe coincidir ±3 dB con
   `|G_estimador(f)|²·R` analítica, sin estructura coherente.
3. **Blancura de innovaciones** (Ljung-Box p > 0,01) y **consistencia NIS**
   dentro del IC 95 % de χ². Detecta mal ajuste sin mirar la verdad de tierra.
4. **Energía conservada por tercio de octava** — ±1,5 dB en 10–50 Hz; bajo
   0,211 Hz (fuera de validez del modelo) `u_hat` debe estar **atenuado, no
   amplificado**.

**De Maes et al. (2016):**

5. **Función de transferencia efectiva del estimador** — `H_p̂d(ω)·H_dp(ω)`.
   Donde vale ≈ 1, la estimación **es el dato**; donde cae a 0, **es el prior**.
   *Es el gráfico que mata la objeción «¿no estás inventando?».*
6. **PSD del error en forma cerrada** — Ec. (70), descompuesta (Ec. 67) en
   modelado / excitación estocástica / ruido de medición. Donde el error domina,
   la banda **no es reportable**. Maes muestra que la PSD del error **pica en
   las antirresonancias**.
7. **Test de entrada nula** — alimentar el estimador con ruido de fondo sin
   martillo. Cualquier estructura espectral es **fabricada**. (Ver §5.6 sobre el
   fallback de ventanas pre-trigger.)
8. **Barrido de Q** — mostrar que la estimación en 10–50 Hz es **insensible a Q**
   sobre 2–3 órdenes de magnitud, mientras que bajo 10 Hz cambia radicalmente.

**Test negativo obligatorio:** forzar `q_scale` 100× el óptimo y verificar que la
suite **falla**. Si no falla, la suite no mide nada.

⚠ **Sobre las barras de error.** `deconvolve()` devuelve la diagonal de
`P_smoothed`, pero Maes et al. advierten que bajo error de modelado las
estimaciones dejan de ser insesgadas y **`P` deja de ser la covarianza real** —
queda optimista. Mostrar ±2σ en la UI como indicador, pero **la barra de error
defendible en la tesis se calcula por la Ec. (70), no se lee de `P`**.

---

## 8. Dónde se engancha respecto del Butterworth

**Default: KF sobre la señal cruda; Butterworth después, solo para visualizar.**
Conmutable a `pre` / `off`, con *warning* cuando se elige `pre`.

Razones, para que la pregunta no vuelva:

1. **`sosfiltfilt` es no causal** (pasada adelante + atrás). No hay
   representación en espacio de estados causal que el KF pueda usar como modelo
   de medición: meterlo «dentro del modelo» es literalmente imposible tal como
   se aplica hoy.
2. **Filtrar antes colorea el ruido.** El KF asume R blanco y quedaría
   sistemáticamente sobre-confiado justo en la banda de paso.
3. **Filtrar antes borra la información fuera de banda** que el estimador usa
   para desambiguar (el modelo medido tiene dinámica en 120–290 Hz).
4. **Doble corrección**: el Butterworth atenúa 1–10 Hz y el KF intenta amplificar
   ahí; en ese orden el resultado no corresponde a ningún modelo físico.

**Excepción: el notch armónico va antes.** La red de 50 Hz cae **dentro** de la
banda útil; si no se quita, el KF la atribuye a movimiento del suelo y la
deconvoluciona con toda la ganancia del modelo. Y `harmonic_notch` no es un IIR
sino una **resta determinística por mínimos cuadrados** sobre la captura
completa, así que no colorea el ruido ni introduce fase.

```
raw ADC → resample_signal → nan_to_num → DC → harmonic_notch
        → [KF forward + RTS backward] → â_ground / v̂_ground ± 2σ
        → [opcional: sosfiltfilt solo para graficar]
```

El módulo **no resamplea**: recibe `y` ya en su fs final. Y usa
`fs_real = fs·up/down`, no `target_fs`, porque `resample_signal` usa
`limit_denominator(1000)` y las dos pueden diferir.

---

## 9. El encuadre honesto de qué aporta

Con `lp_pga_medido`, *en 10–50 Hz* el problema se reduce a invertir la respuesta
de 2º orden conocida del geófono — es decir, **la deconvolución estándar de
respuesta instrumental** de la sismología. Un KF+RTS bien planteado **converge
exactamente a eso**. Con `comp_nominal` no queda ni eso: la cascada ya es plana
y el KF es un denoiser óptimo.

Su valor agregado real **no es la exactitud** sino que entrega la **covarianza
del error** y, con las Ecs. (67)–(70) de Maes, permite decir banda por banda
cuánto de la estimación es dato y cuánto es supuesto.

Presentarlo así es un argumento de tesis **mucho más sólido y más difícil de
atacar** que «usé un método más sofisticado». Por eso el baseline obligatorio de
comparación es la **deconvolución instrumental directa filtrada**, no solo el
ADC crudo.

---

## 10. Riesgos abiertos

| Riesgo | Síntoma observable | Mitigación |
|---|---|---|
| Drift de baja frecuencia inventado | Pico coherente en 1–2,5 Hz en `u_hat`; energía 1–10 / 10–50 Hz mucho mayor que en el crudo | `leaky_rw`; TEST 2; suite anti-invención (§7) |
| Realización numéricamente rota | `min eig(P) < 0`; FRF del modelo realizado no cierra contra el zpk | Forma modal + balanceo; Joseph; fallback UD/Bierman-Thornton |
| Aliasing de modos rápidos | Resonancia falsa dentro de banda tras discretizar | `residualize_fast_modes`; `discretize_plant` lanza `ValueError` si `\|λ\| > 0,8·π·fs` |
| Transitorio inicial confundido con señal | Rampa de baja frecuencia en los primeros ~cientos de ms | `P0` = covarianza estacionaria (`solve_discrete_lyapunov`), **no** `1e6·I`; marcar `burn_in` |
| Comparación injusta KF vs RTS | El RTS «gana» muchísimo por retardo de grupo, no por calidad | Métrica primaria `rmse_aligned`, con `lag_samples` reportado al lado |
| Jitter de trigger contamina la fase | PWS suprime señal real; coherencia entre trazas baja a alta frecuencia | **Medir el jitter antes de S6**; probar μ=1 antes que μ=2 |
| Modelo nominal aplicado a datos reales | Blancura y reconstrucción fallan | Es lo **esperado** (§5.2) — reportarlo, no «arreglarlo» |

---

## 11. Integración web (fase posterior, S5)

**No hacer todavía.** Notas para cuando llegue:

- La app activa es la web en `server/`. Receta de un tab nuevo: botón en
  `static/index.html`, `<section id="panel-<data-tab>">`, módulo
  `static/js/tabs/<nombre>.js` con `mount(root)`, registro en `main.js`, y
  backend `server/<nombre>.py` + `server/routers/<nombre>.py` registrado en
  `api.py`. Plantilla más limpia: el trío de **Filtros**.
- **El KF+RTS va en el tab Filtros; el picking Kalman en MASW.**
- ⚠ **No escribir archivos de estado directamente.** Todo write pasa por
  `server/state.py` (lock, revisión, escritura atómica, HTTP 409 en conflicto).
- Controles a exponer, todos como checkbox o combo: selección de GEO y de
  CONDITIONER, modelo de entrada, magnitud de salida, método de discretización,
  origen de Q y de R, posición del Butterworth, y qué brazos mostrar.

---

## 12. Bitácora

Append-only. Lo más nuevo abajo. Formato:
`## AAAA-MM-DD — sesión — agente`. **Una tarea no se marca hecha en
`TAREAS_KALMAN.md` sin pegar acá la salida numérica del comando.**

### 2026-08-17 — S0 — Claude (Opus 5)

Sesión de investigación y verificación. **No se escribió código todavía**, por
decisión explícita: primero los documentos de continuidad.

Qué se hizo:

- Se mapeó la interfaz (`src/interfaces/python`) y se corrigieron dos supuestos:
  la app activa es la **web**, no la PyQt; y el pipeline **no usa FIR** sino
  Butterworth SOS `sosfiltfilt`.
- Se localizó la **fuente autoritativa** del modelo del AFE
  (`informe_identificacion.txt`, tanda calibrada 21/07/2026) y se verificaron
  los coeficientes de `LP_PGA` leyendo el archivo directamente.
- Se calcularon polos y ceros de `LP_PGA`: **1 cero RHP** en +0,0605 rad/s,
  **2 cuasi-cancelaciones**, y un par de polos **por encima de Nyquist** en todas
  las fs. Salida numérica en §5.4.
- Se midió la respuesta en frecuencia del AFE medido y se describió como «plano,
  sin muesca». **Corregido más tarde en la misma sesión**: la muesca existe, es
  de **2,31 dB** contra **26,13 dB** del nominal ideal (§5.3). La descripción
  original era imprecisa; el número correcto reproduce el hallazgo de 26,33 dB
  ya documentado en el repo.
- **Corrección de fondo del usuario, incorporada:** el modelo del sensor es la
  forma en **aceleración** `H_a = −G·s/(s²+2ζ₀ω₀s+ω₀²)`, no la de velocidad en
  `s²`. Se verificó contra **Ma et al. 2023, Sensors 23:3082** (texto completo).
  Consecuencias: `q = 1` en vez de 2, grado relativo 2 (nominal) / 4 (medido) en
  vez de 3, y la entrada estimada es **aceleración**.
- Se verificó que el compensador nominal **cancela los polos del geófono por
  diseño** y que la cascada nominal colapsa a **1 cero / 3 polos, plana dentro
  de 0,07 dB entre 0,1 y 20 Hz** (§5.2).
- Se hizo la revisión bibliográfica y se armó `REFERENCIAS_KALMAN.md`. Dos
  correcciones que evitan errores de cita: **la URL de Frontiers y Xu et al.
  (2024) son el mismo artículo**, y **el Kalman por bins de frecuencia es de
  Wang, Sun & Wu (2021)**, no de Xu 2024.
- Se verificó que **no existe registro de ruido de fondo dedicado** en
  `data/raw`, y se dejó escrito el fallback (§5.6).

Qué quedó pendiente: todo el código. Empezar por S1
(`models.py`, `library.py`, `plant.py`, `reduce.py` y los catálogos JSON).

### 2026-08-17 — S1 — Claude (Opus 5)

**S1 cerrada: las 13 filas verificadas y verdes.** Módulo
`geophone_scope/kalman_deconv/` con `models.py`, `library.py`, `plant.py`,
`reduce.py`, `cli.py`, `build_catalog.py` y el catálogo en `data/`.

Matriz completa, dos acondicionadores × tres fs:

```
comp_nominal  fs=1020 -> TODO PASA      lp_pga_medido fs=1020 -> TODO PASA
comp_nominal  fs=2604 -> TODO PASA      lp_pga_medido fs=2604 -> TODO PASA
comp_nominal  fs=2929 -> TODO PASA      lp_pga_medido fs=2929 -> TODO PASA
validate-external -> VALIDACION EXTERNA OK
```

Realización modal y reducción sobre el medido (`--cond lp_pga_medido --fs 2604`):

```
REALIZACION MODAL: 13 estados, cond(A) = 5.554e+04   (nominal: 5 estados, 3.515e+06)
PODA DE CUASI-CANCELACIONES (tol relativa 0.005)
  polo -14.5136+1622.15j <-> cero -14.7571+1621.76j  (258.2 Hz, delta 2.79e-04)
  polo -14.5136-1622.15j <-> cero -14.7571-1621.76j  (258.2 Hz, delta 2.79e-04)
  polo -0.119656+1.63046j <-> cero -0.116366+1.63299j (0.2602 Hz, delta 2.54e-03)
  polo -0.119656-1.63046j <-> cero -0.116366-1.63299j (0.2602 Hz, delta 2.54e-03)
  orden: 13 -> 9 polos, 9 -> 5 ceros
RESIDUALIZACION  modo |lambda| = 17142.2 rad/s (2728 Hz);  estados 9 -> 7

PASS modal vs zpk           1.000-150.0 Hz  mag max 0.0000 dB  fase max 0.000 deg
PASS modal vs zpk           0.211-1112.0 Hz mag max 0.0000 dB  fase max 0.000 deg
PASS podado vs original    10.000-50.0  Hz  mag max 0.0001 dB  fase max 0.002 deg
PASS podado vs original     1.000-150.0 Hz  mag max 0.0021 dB  fase max 0.056 deg
PASS reducido vs original  10.000-50.0  Hz  mag max 0.0003 dB  fase max 0.002 deg
PASS reducido vs original   1.000-150.0 Hz  mag max 0.0035 dB  fase max 0.056 deg
```

Se podaron **exactamente** las dos cuasi-cancelaciones predichas en S0, y se
residualizó **exactamente** el modo de 2728 Hz. La realización modal reproduce el
zpk a 0,0000 dB: el ajuste de `C` por mínimos cuadrados sobre grilla logarítmica
funciona mejor que la expansión en fracciones simples con polos cuasi-repetidos.

#### Cuatro hallazgos nuevos de esta sesión

**1. La cancelación geófono↔compensador NO es exacta: hay 2 % de desintonía.**
El compensador está sintonizado a **ω₀ = 64,117378 rad/s = 10,2046 Hz**, pero el
SM-24 nominal es de **10,0 Hz**. Los polos del geófono quedan en
−15,708 ± 60,837j y los ceros del compensador en −16,029 ± 62,081j: no coinciden.
Queda un **dipolo residual** con δ relativo = 2,05e−2, que vale 0,26 dB y 4,2° en
la banda útil. Está dentro de la tolerancia de ±2,5 % del SM-24, así que es
esperable — pero **no es despreciable y no hay que podarlo**.
La cascada nominal es entonces **3 ceros / 5 polos**, no 1/3 como estimé en S0
suponiendo cancelación perfecta. El grado relativo sí es 2, como estaba previsto.

**2. Por eso `cancel_tol_ratio` bajó de 5e−2 a 5e−3.** Con 5e−2 el podador se
comía el dipolo de desintonía (física real) y las verificaciones fallaban con
0,26 dB / 4,18° en 10–50 Hz. Los artefactos genuinos del ajuste de `LP_PGA`
tienen δ = 2,8e−4 y 2,5e−3, un orden de magnitud por debajo. Con 5e−3 se podan
los artefactos y se conserva la física. **El criterio de aceptación es el que
fija la tolerancia, no al revés.**

**3. El corte de residualización va justo debajo de Nyquist, no en 0,4·Nyquist.**
Con 0,4·π·fs, a fs = 1020 el residualizador se llevaba los polos de 266 y 291 Hz,
que están **por debajo** de Nyquist (510 Hz) y hay que discretizar normalmente.
Costo: 2,78 dB y 21,7° en el borde alto de la banda. Corregido a **0,9·π·fs**: la
residualización existe para sacar lo que la discretización aliasaría, no para
simplificar el modelo.

**4. El chequeo cruzado `GEO_LP` detectó la diferencia de normalización, y es
exactamente la predicha.** Fase 0,000° y magnitud desviada en una **constante**
de **+5,2655 dB**. Causa: `GEO_LP` viene con la normalización del informe
(numerador `ζω₀·s`, constante 15,7080) mientras el catálogo usa
**G₀ = 28,8 V·s/m**. El cociente 28,8/15,708 = 1,833465 → **+5,2655 dB**, que
coincide con lo medido hasta el cuarto decimal. `verify_frf` ahora tiene
`align_gain` para comparar **forma** y reportar el offset aparte; con eso el
chequeo pasa a 0,0000 dB / 0,000°, que es la confirmación de que la composición
`Hgeo × LP_PGA` está bien hecha.

#### Validación externa contra Ma et al. (2023)

```
  sin compensar    -3 dB en   1.4378 Hz    ripple 1-100 Hz =  24.35 dB
  compensado       -3 dB en   0.4731 Hz    ripple 1-100 Hz =   0.95 dB
  corte analitico esperado w0/(2*zeta1) = 0.5000 Hz
```

La **planitud** reproduce el paper (0,95 dB de ripple en 1–100 Hz contra su
"respuesta plana"), y el corte cae en el analítico ω₀/(2ζ₁) = 0,50 Hz. El paper
informa **0,8–0,9 Hz**, pero ése es su valor **medido sobre el circuito real**:
la brecha contra el analítico es la misma clase de diferencia nominal-vs-medido
que este proyecto encontró en su propio compensador, no un error del modelo.
El criterio del test valida contra el analítico y lo deja documentado.

#### Detalles de implementación que conviene no re-descubrir

- `second_order_poles` resuelve `s²+2ζω₀s+ω₀²` en forma cerrada, y **obtiene la
  raíz lenta por el producto de raíces** (`ω₀²/raíz_rápida`). Con ζ₁ = 937 la
  resta directa `−ζω₀ + ω₀√(ζ²−1)` sufre cancelación catastrófica justo en la
  raíz que importa (la de 5,44 mHz, que es la extensión de banda).
- `zpk_freqresp` evalúa factor por factor, nunca expandiendo el polinomio.
- Los presets del catálogo están protegidos: `save_*` exige `overwrite=True`.
- `build_catalog.py` es el **único** lugar que llama `np.roots` sobre los
  polinomios de orden 11 y 13, y congela el hash del informe:
  `1540db88313cc1fa73ab7e4c9e37f1a643309ab9efcec7aa5baba29439ae71c4`.

#### Revisión de cierre de S1 — tres correcciones más

Una auditoría del checklist encontró tres ✅ que no cumplían la regla dura.
Corregidas antes de pasar a S2:

**5. El cero RHP se REFLEJA, no se borra — y borrarlo era un bug real.**
La ruta `--prune-rhp` nunca se había ejecutado. Al correrla, falla feo:

```
FALLA podado vs original  10.000-50.0 Hz  mag max 74.3045 dB  fase max 0.033 deg
```

Fase perfecta, magnitud 74 dB afuera. La causa: el cero está en 0,0096 Hz, tres
décadas **debajo** de la banda, así que en 10–50 Hz el factor `(s − a)` vale
prácticamente `s`. Borrarlo equivale a **quitar un derivador**: cambia la
pendiente 20 dB/década. No es un cambio de escala, y por eso ninguna constante de
ganancia lo arregla — mi `_match_gain_at` intentaba justamente eso.

Lo correcto es **reflejar** `a → −a`. El factor `(s−a)/(s+a)` es **pasa-todo**:
conserva `|H|` exacto en toda frecuencia y solo corre la fase en `−2a/ω`, que a
10 Hz son 0,11°. Y de yapa deja el modelo de **fase mínima**, con lo cual el
inverso causal pasa a ser estable. Resultado tras el arreglo:

```
REFLEXION DE CEROS RHP (por debajo de 0.211 Hz)
  cero RHP reflejado: 0.0605229+0j -> -0.0605229+0j  (0.009633 Hz)
PASS podado vs original  10.000-50.0 Hz  mag max 0.0001 dB  fase max 0.063 deg
PASS podado vs original   1.000-150.0 Hz  mag max 0.0021 dB  fase max 1.072 deg
```

La función pasó a llamarse `reflect_rhp_zeros` (con `prune_rhp_zeros` como alias
para no romper imports). **Ojo para S2:** reflejar cambia la fase, así que para
el estudio de fase no mínima (TEST 3 / `--case nmp`) hay que correr con la
reflexión **apagada**, que es el default.

**6. `cond(A) < 1e5` no era un criterio válido.** El nominal de 5 estados da
`cond(A) = 3,515e+06` y aun así reproduce el zpk a 0,0000 dB; el medido de 13
estados da `5,554e+04`. El indicador correcto es la coincidencia de la FRF, no el
número de condición. `cond(A)` se **reporta**, no decide.

**7. El chequeo de ζ estaba mal planteado.** Decía «cambiar ζ no cambia la
ganancia en 25 Hz», y eso es falso incluso en la forma correcta: a 2,5 veces la
resonancia el ζ del **denominador** mueve |H| casi 1 dB, y eso es física. El
invariante real es la **asíntota de alta frecuencia** (`|H_a| → G/ω`, sin ζ):

```
zeta 0,25 -> 0,60:  en 25 Hz -0.988 dB (esperable, es el denominador)
                    en la asintota 500 Hz -0.0021 dB  [OK]
                    (la firma de acoplamiento de normalizacion seria +7,60 dB)
```

Y se agregó `--dump-config`, que la fila 1.1 citaba sin que existiera.

#### Estado final de S1

```
comp_nominal  fs=1020/2604/2929 -> TODO PASA
lp_pga_medido fs=1020/2604/2929 -> TODO PASA
validate-external -> OK        dump-config -> OK
```

Siguiente: **S2** — `discretize.py`, `kf.py`, `synthetic.py`. Los TEST 1, 2 y 3
de §6 son lo primero, porque condicionan si la formulación de estado aumentado
sirve o hay que pasar a Dual KF.

⚠ **Trampa que espera en TEST 1:** bajo ZOH los parámetros de Markov discretos
**nunca son exactamente cero**. Para grado relativo `r` valen
≈ `(Tʳ/r!)·CA^(r−1)B`: chiquísimos pero no nulos. Un test de cero exacto va a
encontrar `i = 0` y «fallar» sin motivo. Hay que correr TEST 1 sobre las matrices
**continuas**, o usar un umbral **relativo** con la escala `Tʳ` como firma
esperada.
