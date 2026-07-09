# MASW — proyectos afines y qué conviene ensamblar acá

Relevamiento (2026-07-08) de herramientas open-source de ondas superficiales para
ver qué se puede "enchufar" al flujo de `field_review_app.py` (picking multi-modo
nativo + `masw_backends.py` como router de motores/exportadores). Ordenado por
relación valor / esfuerzo. El diseño de `masw_backends` (registro de backends
in-proc + exportadores) está pensado justamente para sumar estos sin tocar la UI.

## Ya integrado
- **disba** (forward, Numba) + **evodcinv** (inversión evolutiva multi-modo) —
  motores `evodcinv` y `disba_mc`.
- **maswavespy** (port numpy, `masw_inversion.py`) — motor `maswavespy` (1 modo; el
  paquete real necesita compilar Cython). Su `combination.CombineDCs` (numpy) se usa
  para exportar/combinar.
- **swprepost** (Vantassel) — export a **Dinver `.target` real** (XML gzip) por modo,
  reemplazando el texto hecho a mano. `pip install swprepost`. ← recién sumado.

## Recomendado sumar (alto valor)

### 1. BayHunter — inversión bayesiana transdimensional (McMC)  ★ tesis
Repo: https://github.com/jenndrei/BayHunter  ·  forward: SURF96 (CPS) vía `pysurf96`.
Qué aporta que hoy no tenemos: **cuantificación de incertidumbre** (posterior de
Vs-profundidad), el **número de capas como incógnita** (no hay que fijarlo), y
BayWatch para ver la inversión en vivo. Para una tesis, poder mostrar bandas de
incertidumbre del perfil Vs es un salto de calidad frente al "mejor modelo" puntual.
Enchufe: nuevo backend `bayhunter` en `masw_backends` que reciba `curves_by_mode`
(+ velstd) y devuelva un perfil medio + percentiles; el display ya soporta el dict
normalizado, habría que agregarle bandas. Esfuerzo: medio (pysurf96 puede requerir
gfortran en Windows; si no compila, queda como export/receta).

### 2. swprocess — procesamiento MASW en Python con incertidumbre de la curva
Repo: https://github.com/jpvantassel/swprocess  ·  pip.
Hace la imagen de dispersión (FK, phase-shift, slant-stack) sobre varias ventanas y
saca **media ± std de la curva** (workflow SWprocess/SWinvert). Dos usos:
(a) **validar** nuestra imagen phase-shift casera contra otra implementación;
(b) obtener `velstd` por frecuencia y pasárselo a evodcinv (`Curve(uncertainties=...)`)
y a BayHunter → inversiones con pesos reales en vez de rmse plano.
Enchufe: motor/analizador opcional que consuma el gather (mismo `matrix, distances,
common_time`) y devuelva `(freq, c, cstd)` por modo. Esfuerzo: medio.

### 3. obspy — I/O sísmico (SEG-Y / SU) y filtrado robusto
Repo: https://github.com/obspy/obspy  ·  pip.
Exportar el **gather crudo** a SEG-Y/SU deja que Geopsy (u otras) hagan su propio
picking de dispersión sobre las trazas, no solo importar nuestra curva. También da
lectura/escritura y filtros estándar. Enchufe: un exportador `segy`/`su` en
`export_curves`/`export_raw`. Esfuerzo: bajo (export), pero requiere pasar el gather
crudo, no la curva.

## Opcionales / nicho
- **pysurf96** (surf96/CPS forward): cross-check del forward de disba (¿coinciden las
  curvas teóricas?). Bajo-medio. Base de BayHunter, así que entra con (1).
- **ADsurf** (github.com/liufeng2317/ADsurf): inversión multimodal por diferenciación
  automática (también forward disba). Hoy exportamos para él; vendorizarlo para correr
  in-proc es posible pero es código de investigación con sus deps. Bajo.
- **DisbaTomo**: tomografía 2D/3D de Vs con disba — otro caso de uso (perfil lateral),
  no 1-D. Fuera de alcance por ahora.
- **CC-FJpy** (frequency-Bessel): extracción de modos superiores de ruido ambiental.
  Útil si algún día se usa ruido pasivo en vez de martillo. Fuera de alcance.

## Sugerencia de orden
1. (hecho) swprepost → `.target` de Dinver.
2. swprocess → `velstd` por modo (mejora evodcinv y prepara BayHunter).
3. BayHunter → backend bayesiano con bandas de incertidumbre (lo más "tesis").
4. obspy → export SEG-Y/SU del gather crudo para Geopsy/otros.

Fuentes: swprocess/swprepost/SWinvert (Vantassel & Cox, Univ. Texas / Virginia Tech),
BayHunter (Dreiling & Tilmann, GFZ), disba/evodcinv (Luu), MASWavesPy (Olafsdottir).
