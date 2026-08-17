# Referencias — deconvolución Kalman+RTS y picking de dispersión

Bibliografía verificada para el trabajo descrito en `HANDOFF_KALMAN.md`.

**Cómo leer esta tabla.** Cada entrada lleva un estado explícito:

- **VERIFICADO** — se encontró la fuente y dice lo que acá se afirma. Cuando
  dice «texto completo», se leyó el artículo; cuando dice «metadatos», se
  confirmó autoría/revista/DOI pero **no** el contenido interno.
- **PROBABLE** — consistente con la literatura, pero no se verificó la fuente
  primaria. **Verificar antes de citar en la tesis.**
- **NO VERIFICADO** — se menciona porque alguien lo va a buscar, pero no está
  confirmado. **No citar.**

Esta distinción no es burocracia: es lo que evita que una cita inventada llegue
al documento final.

---

## 1. Modelo del sensor y del acondicionador

| Ref. | Estado | Qué aporta |
|---|---|---|
| **Ma, Wu, Ma, Xu, Qi & Jiang (2023).** *An Effective Method for Improving Low-Frequency Response of Geophone*. **Sensors 23:3082.** DOI [10.3390/s23063082](https://doi.org/10.3390/s23063082) · [PMC10059280](https://pmc.ncbi.nlm.nih.gov/articles/PMC10059280/) | **VERIFICADO — texto completo** | **La referencia base del modelo.** Ver §1.1 |

### 1.1 Lo que dice Ma et al. (2023), verificado

Respuesta del geófono a **aceleración** del suelo (entrada = aceleración,
salida = tensión) — **ésta es la forma que se usa en este proyecto**:

```
H_a(s) = −G·s / (s² + 2γ₀ω₀s + ω₀²)
```

Respuesta a **velocidad** (entrada = velocidad). **No se usa acá**, se anota
para que nadie las mezcle:

```
H_v(s) = −G·s² / (s² + 2γ₀ω₀s + ω₀²)
```

Compensador de extensión de banda — pasa-todo menos pasa-banda, realizado en
Sallen-Key con restador:

```
H_compensate(s) = 1 − 2(γ₁−γ₀)ω₀·s / (s² + 2γ₁ω₀s + ω₀²)
                = (s² + 2γ₀ω₀s + ω₀²) / (s² + 2γ₁ω₀s + ω₀²)
```

Las dos expresiones son algebraicamente idénticas. La segunda deja ver lo
esencial: **el numerador del compensador cancela los polos del geófono por
diseño**. Sube el amortiguamiento efectivo de γ₀ a γ₁ preservando ω₀.

Parámetros del paper: geófono JF-20DX, f₀ = 10 Hz, sensibilidad
0,273 V/(cm/s), bobina 395 Ω, masa móvil 11 g, γ₀ = 0,3 → γ₁ = 10. Resultado
reportado: el punto de −3 dB baja a **0,8–0,9 Hz**, con respuesta plana en
1–100 Hz y sensibilidad ≈ 194 mV/(m/s²).

**Uso en este proyecto:** el preset `comp_ma2023` reproduce estos valores y el
resultado del paper es el **test de validación externa** del constructor de
modelos (S1). Si no se reproduce el −3 dB en 0,8–0,9 Hz, el constructor está
mal y todo lo que venga después es inútil.

### 1.2 Correspondencia con la documentación interna

El `informe_identificacion.txt` de la tanda calibrada escribe
`Hgeo = ζ·ω0·s/(s² + 2ζω0·s + ω0²)`. **Es la misma forma en aceleración de Ma
et al.**, salvo que la constante del numerador es `ζω₀` en vez de `G`. Son
proporcionales, pero ⚠ **en la normalización del informe el numerador contiene
ζ**: cambiar ζ de 0,25 a 0,60 mueve también la ganancia (×2,4 ≈ 7,6 dB). En la
forma de Ma no. Por eso cada modelo del catálogo guarda su campo `forma`.

Fuente autoritativa de los coeficientes medidos:

```
C:\Github\Tesis\src\calculos_modelados\matlab\AnalisisCircuito\
    resultados_tanda_calibrada\05_tablas_reportes\informe_identificacion.txt
```

⚠ La tanda del **20/07/2026** (`Osciloscopio_descartado_2026-07-20_BP_43k`, con
Rbp = 8200 Ω) está **descartada**. Su `zeta_realizada_componentes = 83,661` **no
es un objetivo de diseño** — es lo que realizaba el circuito con la resistencia
equivocada. La tanda calibrada del 21/07 da 0,25. Lectura autoritativa en
`docs/Primera Presentación/latex-historial/secciones/09_etapa_paper_caracterizacion.tex`.

---

## 2. Estimación conjunta entrada-estado (KF, RTS, AKF, JIS)

| Ref. | Estado | Qué aporta |
|---|---|---|
| **Rauch, Tung & Striebel (1965).** *Maximum likelihood estimates of linear dynamic systems*. **AIAA Journal 3(8):1445–1450.** DOI [10.2514/3.3166](https://doi.org/10.2514/3.3166) | **VERIFICADO** | El smoother original. Su propio ejemplo numérico ya muestra la ventaja del suavizado sobre el filtrado — es la justificación de base del procesamiento offline |
| **Xi, Yuan, Yang, Song, Fang & Chen (2025).** *Modified extended Rauch–Tung–Striebel smoother method for the dynamic external excitation identification of piezoelectric vibration energy harvesting systems*. **MSSP 224:111964.** DOI [10.1016/j.ymssp.2024.111964](https://doi.org/10.1016/j.ymssp.2024.111964) | **VERIFICADO** (metadatos + abstract) | El precedente análogo. Ver §2.1 — **límites importantes** |
| **Lourens, Reynders, De Roeck, Degrande & Lombaert (2012).** *An augmented Kalman filter for force identification in structural dynamics*. **MSSP 27:446–460.** DOI [10.1016/j.ymssp.2011.09.025](https://doi.org/10.1016/j.ymssp.2011.09.025) | **VERIFICADO** | El AKF canónico: las fuerzas desconocidas entran al vector de estado. ⚠ **circula un DOI erróneo `.11.025`; Crossref devuelve `.09.025`** |
| **Naets, Cuadrado & Desmet (2015).** *Stable force identification in structural dynamics using Kalman filtering and dummy-measurements*. **MSSP 50-51:235–248.** DOI [10.1016/j.ymssp.2014.05.042](https://doi.org/10.1016/j.ymssp.2014.05.042) | **VERIFICADO** | **El paper del drift.** El AKF es inobservable con mediciones tipo aceleración porque «la aceleración es insensible a las componentes de baja frecuencia de la fuerza». Remedio: *dummy-measurements* a nivel de posición |
| **Gillijns & De Moor (2007a).** *Unbiased minimum-variance input and state estimation for linear discrete-time systems*. **Automatica 43(1):111–116.** DOI [10.1016/j.automatica.2006.08.002](https://doi.org/10.1016/j.automatica.2006.08.002) | **VERIFICADO** | JIS **sin** feedthrough. Condición de identificabilidad: rango completo sobre `CB` |
| **Gillijns & De Moor (2007b).** *…with direct feedthrough*. **Automatica 43(5):934–937.** DOI [10.1016/j.automatica.2006.11.016](https://doi.org/10.1016/j.automatica.2006.11.016) | **VERIFICADO** | JIS **con** feedthrough. **No aplica en este proyecto**: grado relativo ≥ 2 ⇒ J = 0 ⇒ `(JᵀR̄⁻¹J)⁻¹` no existe |
| **Eftekhar Azam, Chatzi & Papadimitriou (2015).** *A dual Kalman filter approach for state estimation via output-only acceleration measurements*. **MSSP 60-61:866–886.** DOI [10.1016/j.ymssp.2015.02.001](https://doi.org/10.1016/j.ymssp.2015.02.001) | **VERIFICADO** | Dual KF. Existe precisamente para evitar «la deficiencia de rango de la formulación aumentada» y el drift. **Es el plan B si el TEST 2 falla de forma severa** |
| **Maes, Smyth, De Roeck & Lombaert (2016).** *Joint input-state estimation in structural dynamics*. **MSSP 70-71:445–466.** DOI [10.1016/j.ymssp.2015.07.025](https://doi.org/10.1016/j.ymssp.2015.07.025) | **VERIFICADO — texto completo** | **La referencia más valiosa del conjunto.** Ver §2.2 |
| **Maes, Gillijns & Lombaert (2018).** *A smoothing algorithm for joint input-state estimation in structural dynamics*. **MSSP 98:292–309.** DOI [10.1016/j.ymssp.2017.04.047](https://doi.org/10.1016/j.ymssp.2017.04.047) | **VERIFICADO — texto completo** | JIS **con retardo L**. Dos resultados que se usan acá: el retardo **relaja las condiciones de invertibilidad**, y **reduce la incertidumbre** cuando el sensor no está colocado con la fuerza estimada — que es exactamente el caso |
| **Åström, Hagander & Sternby (1984).** *Zeros of sampled systems*. **Automatica 20(1):31–38.** | **VERIFICADO** | Con **grado relativo > 2** y muestreo rápido, **el sistema muestreado es siempre de fase no mínima**. La discretización *crea* ceros fuera del círculo unidad que no existen en el continuo |
| **Darouach, Zasadzinski & Xu (1994).** *Full-order observers for linear systems with unknown inputs*. **IEEE TAC 39(3):606–609.** | **VERIFICADO** (metadatos) | UIO clásico. **No recomendado acá**: para el caso lineal offline con ruido gaussiano el marco estocástico es superior porque entrega la covarianza |
| Devasia, Chen & Paden (1996); Bayo (1987); Chen (1993) | **PROBABLE** | Inversión estable **no causal** de sistemas de fase no mínima: la dinámica interna inestable se resuelve *hacia atrás en el tiempo*. **Verificar antes de citar** |
| Floquet & Barbot; Hsieh (JIS con retardo) | **PROBABLE** | Citados **dentro** de Maes 2018 (que sí se leyó); las fuentes primarias no se verificaron |
| Tikhonov / curva-L en identificación de fuerzas | **PROBABLE** | Estándar del área; se reporta que la curva-L puede fallar con dos codos y que Tikhonov tiende a sobre-suavizar. **Verificar cada paper antes de citar** |
| Ceros límite `z²+4z+1` para grado relativo 3 | **NO VERIFICADO** | **Calcularlo numéricamente en vez de citarlo** — es más convincente y no arriesga una cita falsa |

### 2.1 Xi et al. (2025) — qué se sabe y qué NO

**Verificado** (metadatos vía Crossref sobre el DOI; abstract indexado; registro
ADS `2025MSSP..22411964X`): el artículo existe, el DOI es correcto, y el volumen
(224), número de artículo (111964) y año (2025) coinciden.

Del abstract, verificado:

- El método es **ERTSS-PSO**: RTS *extendido* combinado con *particle swarm
  optimization*.
- Identifica la excitación externa **desde la respuesta de voltaje** del
  cosechador piezoeléctrico — un solo canal eléctrico, análogo estructural al
  caso de este proyecto.
- La modificación declarada son **ventanas deslizantes parcialmente solapadas**,
  para eliminar el error entre ventanas contiguas.
- **No requiere conocimiento previo de la estadística de la excitación.**
- Validación: simulación con dos PVEH de rigidez no lineal de 3º y 5º orden +
  experimento con placa laminada piezoeléctrica circular.
- Reporta **≥ 9 % de mejora de precisión** frente a métodos existentes.

⚠ **NO ACCEDIDO — texto completo** (paywall Elsevier; Semantic Scholar devuelve
`abstract: null`). Por lo tanto **se desconocen y NO deben citarse**: la
formulación exacta del estado aumentado, cómo modelan la excitación desconocida,
**qué optimiza el PSO** (no asumir que sintoniza Q y R), la elección de Q y R,
las métricas de validación en detalle, y las limitaciones que declaran.

**Transferibilidad (análisis propio, no del paper).** Es un smoother *extendido*
porque el PVEH es fuertemente no lineal y electromecánicamente acoplado. La
cadena geófono+AFE de este proyecto es **LTI y conocida**: no hace falta la
versión extendida ni el PSO, y el RTS lineal clásico es exacto (óptimo MMSE).
El precedente vale como **prueba de concepto de que un smoother de intervalo
recupera la excitación desde un único canal de salida**, no como receta a copiar.

### 2.2 Maes et al. (2016) — el instrumento anti-fabricación

Es la referencia que da respaldo directo al requisito de «no inventar
información». Lo verificado en texto completo:

- **§2.3 — ceros y polos.** La transferencia de un sistema modal tiene la forma
  `s^q · Π(s²+k₁ₙs+k₀ₙ) / Π(s²+2ξωs+ω²)`, con **q = 0 para
  desplazamiento/deformación, q = 1 para velocidad, q = 2 para aceleración**:
  o sea `q` ceros en el origen. Los polos dependen solo de ω y ξ; **los ceros
  dependen de la configuración de sensores y no son propiedad del sistema**.
  Bajo ZOH `λ_D = exp(λΔt)`, y los ceros se clasifican en estables (|λ_D|<1),
  marginalmente estables (=1) e inestables (>1).
- **Ecs. (67) y (70).** La PSD del error sobre la entrada estimada en **forma
  cerrada**, descompuesta en tres términos: **modelado**, **excitación
  estocástica adicional** y **ruido de medición**. Permite calcular banda por
  banda cuánto de la estimación es dato y cuánto es ruido amplificado.
- **§3.5–3.6, verbatim:** *«Unstable zeros lead to large errors on the estimated
  quantities, due to the amplification of measurement errors and, therefore,
  should be avoided»*. Las configuraciones con ceros inestables dan errores de
  **varios órdenes de magnitud** mayores (Fig. 17).
- La PSD del error **pica en las antirresonancias (los ceros)**: la literatura
  dice de antemano dónde esperar que la estimación sea basura.
- **Fig. 18:** la varianza del error depende fuertemente de la σ asumida para la
  excitación, y **el óptimo cambia según qué cantidad se quiera estimar**.
  Traducción: **Q no es un parámetro libre inocuo**.
- ⚠ **Advertencia crítica:** bajo error de modelado las estimaciones dejan de
  ser insesgadas y de mínima varianza, y **`P` deja de ser la covarianza real
  del error** — queda optimista. Las Ecs. (67)–(70) sí siguen valiendo.
  **Conclusión operativa: la barra de error defendible se calcula
  analíticamente por la Ec. (70), no se lee de `P`.**

---

## 3. Imagen de dispersión y picking

**Los dos problemas son distintos y se evalúan distinto:** *mejorar `P(f,c)`* no
es lo mismo que *extraer `c(f)`*.

| Ref. | Estado | ¿Mejora P(f,c) o extrae c(f)? | Nota |
|---|---|---|---|
| **Park, Miller & Xia (1998).** Phase-shift | — | mejora (línea base) | Lo que ya está implementado en `masw_dispersion.py` |
| **Wang, Sun & Wu (2021).** *Computers & Geosciences* 153:104809. DOI [10.1016/j.cageo.2021.104809](https://doi.org/10.1016/j.cageo.2021.104809) | **VERIFICADO** | **extrae** | **La cita primaria del Kalman por bins de frecuencia.** Ver §3.1 |
| **Xu, Tian, Wu, Xie, Wang & Zhang (2024).** *Front. Earth Sci.* 12:1379668. DOI [10.3389/feart.2024.1379668](https://doi.org/10.3389/feart.2024.1379668) | **VERIFICADO** | extrae + mejora | Reaplica Wang 2021. Ver §3.1 |
| **Cheng, Xia, Zhang, Zhou & Ajo-Franklin (2021).** *GJI* 226(1):256–269. DOI [10.1093/gji/ggab101](https://doi.org/10.1093/gji/ggab101) | **VERIFICADO** | **mejora** | **PWS.** Ver §3.2 |
| **Luo, Xia, Miller, Xu, Liu & Liu (2008).** *PAGEOPH* 165:903–922. DOI [10.1007/s00024-008-0338-4](https://doi.org/10.1007/s00024-008-0338-4) | **VERIFICADO** | **mejora** | HLRT. Reportan >50 % de mejora de resolución vs slant-stack. Complemento sobre modos: Luo et al. (2009), *GJI* 179(1):254–264 |
| **Mun, Bao & Li (2015).** *Generation of Rayleigh-wave dispersion images from multichannel seismic data using sparse signal reconstruction*. *GJI* 203(2):818–827. DOI [10.1093/gji/ggv348](https://doi.org/10.1093/gji/ggv348) | **VERIFICADO** | **mejora** | Sparse L1. La cita original era correcta |
| **Kuang, Pan, Zhang & Yuan (2025).** *GJI* 243(1):ggaf323. DOI [10.1093/gji/ggaf323](https://doi.org/10.1093/gji/ggaf323) | **VERIFICADO** | **extrae** | **E-DBSCAN.** Ver §3.3 |
| **Hou, Yu, Yuan, Fu, Fan, Han, Liu, Qian & Zhou (2025).** *Scientific Reports* 15:21595. DOI [10.1038/s41598-025-04954-w](https://doi.org/10.1038/s41598-025-04954-w) | **VERIFICADO** | **extrae** | **Ridge/Hessiano.** Ver §3.4 |
| **Dong, Li, Chen & Fu (2021).** DisperNet. *BSSA* 111(6):3420–3431 · **Yang et al. (2022).** *SRL* 93(3):1549–1563 (código `DisperPicker`) | **VERIFICADO** | extrae | CNN. **No recomendado**: requieren corpus etiquetado y el dominio de entrenamiento está lejísimos (10–50 Hz, c 70–100 m/s, 21 trazas) |
| **Shen, Wang, Wang, Xu & Cheng (2015).** *Resolution equivalence of dispersion-imaging methods for noise-free high-frequency surface-wave data*. *J. Appl. Geophys.* 122:167–171 | **PROBABLE** (autoría con confianza media) | — | **La advertencia más importante.** Ver §3.5 |
| **Yao et al. (2023).** *Earth and Space Science*. DOI [10.1029/2023EA003198](https://doi.org/10.1029/2023EA003198) | **PROBABLE** | ambos | Combina τ-p de alta resolución con clustering no supervisado |

### 3.1 Wang (2021) y Xu (2024) — dos aclaraciones que evitan un error de cita

**Aclaración 1: la URL de Frontiers `10.3389/feart.2024.1379668` ES Xu et al.
(2024) sobre la cuenca de Qaidam.** No son dos referencias independientes.

**Aclaración 2: el paso de Kalman no es aporte de Xu et al. (2024).** Viene de
**Wang, Sun & Wu (2021)**, y **Dunshi Wu es coautor de ambos**: es el mismo
grupo reaplicando su propio algoritmo. **La cita primaria es Wang 2021.**

Flujo de Wang 2021: umbral de energía → **GMM** → **DBSCAN** en f–v → **pico de
energía por frecuencia** dentro de cada zona modal → **filtrado y suavizado de
Kalman**. Es decir, el Kalman corre **a lo largo del eje de frecuencia, sobre la
secuencia de picks, DESPUÉS del argmax**, como suavizador y rechazo de outliers.

Xu 2024 lo reaplica con redacción ambigua — literalmente solo dice *«Within
different mode regions, Kalman filtering is applied to the dispersion energy to
eliminate noise interference»* y luego *«searching for the maximum value at each
frequency»*. **Ninguno de los dos publica el modelo de estado, la matriz de
transición ni las covarianzas.** No atribuirles un modelo de estado: no existe
publicado.

**Qué queda abierto y es aporte defendible** (esto va a la tesis como
contribución, formulado como *«aplicamos el esquema de Wang et al. (2021),
extendiéndolo con…»*, **nunca** como *«proponemos por primera vez»*):

1. **Formular el modelo de estado.** Se elige **lentitud** `x_f = [p, dp/df]`
   con `p = 1/c`, porque `p(f)` es mucho más suave que `c(f)`.
2. **Meter las cotas físicas del arreglo dentro del filtro** — `c ≥ 2Δx·f`
   (aliasing espacial) y `c ≤ L·f` (resolución) como *gating* de la innovación o
   como restricciones de desigualdad. Nadie lo hace y es específico de arreglos
   chicos. **Es el ángulo más fuerte.**
3. **Invertir el orden: tracker predictivo.** La predicción a priori acota la
   ventana de búsqueda del `argmax` en el bin siguiente. Convierte el picker en
   un seguidor de rama modal robusto a saltos de modo, en vez de un suavizador
   de picks ya errados.
4. **Propagar la incertidumbre**: `P(f)` da barras de error por frecuencia que
   entran **directo como pesos de datos** a la inversión (`evodcinv`/`disba`),
   que hoy no los usa.

**Aplicabilidad.** La adquisición de Xu 2024 es de exploración petrolera:
receptores cada **40 m**, subconjuntos de **50 trazas**, aperturas de ~2 km. La
interpolación wavelet ×4 existe porque tienen Δx = 40 m; **con Δx = 2 m no
aporta nada**. Y el GMM 3D sobre una imagen de 21 trazas es mucho más frágil que
con 50.

### 3.2 PWS (Cheng et al. 2021) — el mejor costo/beneficio de imagen

Peso de coherencia de fase instantánea, calculado para cada (f₀, v) del barrido:

```
ω(f₀,v) = | (1/N) Σⱼ Ψⱼ(f₀,v)/|Ψⱼ(f₀,v)| |^μ ,   μ ≥ 1
```

con `Ψⱼ` el espectro complejo desplazado en fase de la traza j. **El peso ignora
la amplitud**, y por eso ataca directo los lóbulos laterales de la respuesta del
arreglo y el ruido incoherente. Es un factor multiplicativo sobre el mismo
barrido que ya se hace: **costo casi nulo**.

⚠ **Advertencia específica de este rig, que ningún paper cubre.** Todos los
trabajos asumen arreglo **físico y simultáneo**: N geófonos, un disparo. Acá el
arreglo es **sintetizado**, con un disparo distinto por posición. El jitter de
trigger, la no-repetibilidad del martillo y la variación de acople meten fase
espuria, y **PWS la penaliza más** que los métodos de energía: **puede suprimir
señal real**. Antes de invertir en PWS o HLRT hay que **cuantificar el jitter de
trigger**; si es fracción significativa del período a 50 Hz (20 ms), la fase
alta está comprometida y ningún método de imagen lo arregla. Probar μ = 1 antes
que μ = 2 y comparar contra phase-shift sobre la misma captura.

### 3.3 E-DBSCAN (Kuang et al. 2025)

DBSCAN clásico decide si un punto es *core* **contando** vecinos dentro de ε
(*MinPts*); E-DBSCAN **suma los valores de energía** de esos vecinos
(*MinEnergy*). Así una zona de muchos píxeles débiles ya no forma cluster.
Modificación trivial de implementar.

⚠ El aporte central del paper (procesar componentes Z y X por separado y
combinar) **no aplica** con un solo componente vertical. La modificación
energética sí. Sus datos activos (48 receptores @1 m, offset mínimo 6 m) son de
escala comparable a este proyecto — buena noticia.

### 3.4 Ridge/Hessiano (Hou et al. 2025) — el mejor upgrade inmediato del picker

Filtro gaussiano multiescala → puntos de cresta por **autovalores del Hessiano**
(detector tipo Frangi) → conexión por proximidad y continuidad direccional →
fusión de segmentos → orden modal. **Sin entrenamiento y sin interacción
manual**, del modo fundamental al 8º. Resuelve exactamente el fallo del `argmax`
actual: saltar de rama modal.

### 3.5 Shen et al. (2015) — el límite es la apertura, no el algoritmo

Resultado central: **para datos sin ruido, los métodos de imagen de dispersión
son equivalentes en resolución.** La ganancia de HLRT y sparse-L1 aparece con
ruido y arreglos irregulares, **no como superresolución**.

Con L = 40 m, `Δc ≈ c²/(f·L)`: a 10 Hz y 90 m/s son ~20 m/s. **HLRT y sparse-L1
van a afilar el pico visualmente pero no crean información; el riesgo real es
afilar un pico sesgado y creerle.** Por eso entran al plan como **comparación
metodológica obligatoria** — mostrar que no mejoran es un resultado publicable —
y no como pipeline de producción.

Corolario relacionado: con L = 40 m en 10–50 Hz es dudoso que haya **modos
superiores** resolubles. No perseguir lo que la física del arreglo no separa.

---

## 4. Fuentes internas del proyecto

| Documento | Qué aporta |
|---|---|
| `src/calculos_modelados/matlab/AnalisisCircuito/resultados_tanda_calibrada/05_tablas_reportes/informe_identificacion.txt` | **Fuente autoritativa** de los coeficientes medidos (BP, COMP, LP, LP_PGA, GEO_LP), los parámetros del compensador y el modelo del operacional |
| `.../resultados_tanda_calibrada/RESULTADO.md` | Informe en prosa; el −57,582 dB predicho vs −31,26 dB medido y la diferencia de 26,33 dB |
| `docs/Primera Presentación/latex-historial/secciones/09_etapa_paper_caracterizacion.tex` | **Lectura autoritativa de los tres valores de ζ**; resuelve la confusión del 83,661 |
| `docs/Primera Presentación/latex-historial/secciones/24_banda_util_real.tex` | La banda útil real 10–50 Hz, verificada por cinco caminos independientes |
| `docs/Primera Presentación/latex-historial/secciones/28_sev_guiada.tex` | Verificación de `data/Moldeo Hidro` y recomendación de adoptarla como curva de trabajo |
| `docs/Primera Presentación/latex/secciones/02b_geofono.tex` | Parámetros del SM-24 y el desmentido explícito del 4,5 Hz |
| `docs/Primera Presentación/latex/secciones/03b_topologias.tex` | Comparativa contra Ma et al. 2023 y la tabla de pérdida de sensibilidad por ζ₁ |
| `geophone_scope/HANDOFF_FIELD_REVIEW.md` | Historia y razones del pipeline de filtrado y de la convención de polaridad |
