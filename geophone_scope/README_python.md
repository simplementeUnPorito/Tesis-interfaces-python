# Geophone Scope — Python/PyQt6

GUI de escritorio (reemplaza `InterfaceESP.m`). Funcionalmente equivalente a la
interfaz MATLAB. Para trabajo de campo se prefiere la UI web del maestro ESP32;
esta app es útil para post-proceso y análisis en PC.

## Requisitos

- Python 3.11+
- Paquetes en `requirements.txt`

## Instalación

```bash
cd src/python/geophone_scope
pip install -r requirements.txt
```

## Ejecutar

```bash
python main.py
python main.py --port COM8
python main.py --port COM8 --baud 921600
python main.py --log-dir C:\Logs --data-dir C:\Data
```

## Convertir ZIP de la web a .mat

```bash
python zip_to_mat.py capture.zip
```

Convierte el ZIP exportado por la UI web del maestro en un `.mat` con las mismas
claves que la app de escritorio (`node1_raw`, `node1_filt`, `fs`, etc.).

## Revisar muestras de campo de Canchita

La herramienta `review_field_data.py` revisa carpetas exportadas por la web,
detecta duplicados comparando los puntos exactos de las señales crudas (primero
carpetas completas y después captura por captura, así atrapa la misma captura
repetida en carpetas con nombre distinto), marca un trigger inicial del hammer,
y abre una GUI para corregir ese cursor y la distancia de cada muestra. Por defecto
usa `raw_f32le.bin`; si alguna vez querés revisar las señales filtradas podés
agregar `--filtered`.

```bash
cd C:\Github\Tesis\src\python\geophone_scope
.\.venv\Scripts\python.exe review_field_data.py --scan-only
.\.venv\Scripts\python.exe review_field_data.py
```

En la ventana:

- mover el cursor naranja en la curva del hammer para ajustar el golpe;
- también podés ajustar el trigger con teclado: `←`/`→` mueve 1 muestra,
  `Shift+←/→` mueve 10 muestras y `Ctrl+←/→` mueve 1 ms;
- `Marcar zona auto` permite hacer dos clicks en la curva del hammer para
  limitar dónde busca el trigger automático;
- `Auto` recalcula el trigger de la señal actual dentro de esa zona; `Auto
  visibles` lo aplica a todas las filas visibles del filtro activo, útil para
  una tanda como `16 m`;
- la curva del geófono se muestra en tiempo relativo a su hammer (`t=0` en el
  trigger); las señales superpuestas del mismo label también se desplazan por
  su propio trigger para confirmar visualmente si el label está bien;
- editar `Distancia m` si la etiqueta original estaba mal;
- usar `Aplicar dist. a carpeta` cuando todas las capturas de esa carpeta tienen
  la misma distancia corregida;
- usar `Invertir geo de carpeta` si el geófono de esa tanda quedó conectado al
  revés (el circuito no tiene polaridad): invierte el geo de **todas** las
  capturas de la carpeta actual (es un toggle y queda guardado en las marcas,
  se aplica también a promedios, waterfall, MASW y export);
- la zona auto marcada con dos clicks se limpia sola al cambiar de muestra;
- desmarcar `Usar esta muestra` para excluir golpes malos;
- usar `Filtro` para ver `Sin revision` o `Marcadas con N metros`;
- usar `Mostrar mismo label` para filtrar la tabla al label actual y superponer
  las señales de esa misma distancia;
- usar `Guardar y siguiente` para marcar la muestra como revisada, guardarla y
  saltar a la próxima visible del filtro activo;
- alternar `Modo oscuro` / `Modo claro` según convenga;
- la ventana tiene seis pestañas: `Capturas` (la revisión golpe a golpe de
  arriba), `Filtros`, `Enfase`, `Promedios / arrivals`, `Waterfall` y `MASW`.
  El botón `Ir a promedios / arrivals` cambia de pestaña (ya no abre una
  ventana aparte);
- **polaridad fija**: al cargar, el geófono queda siempre NO invertido y el
  hammer siempre invertido, usando el flag `invert_signal` de la metadata solo
  para saber cómo vino guardada cada señal (si un geo vino invertido se
  desinvierte; si un hammer vino sin invertir se invierte);
- la pestaña `Enfase` corrige errores chicos de posicionamiento entre tandas
  medidas en días/carpetas distintas con el mismo label: elegís el label, ves
  todas sus trazas superpuestas (un color por carpeta) y le das a cada carpeta
  un offset en ms (positivo = esa tanda se corre a la izquierda). Los offsets
  entran en promedios, waterfall, MASW y export, y persisten en
  `Crudos\Canchita\alignment_offsets.json`;
- la pestaña `Filtros` define un pasa-banda Butterworth aplicado con `filtfilt`
  (fase cero, no corre los triggers) con corte bajo/alto y orden a elegir; un
  corte en 0 desactiva ese extremo. La vista previa muestra la captura actual
  de `Capturas` en tiempo y en espectro (original vs filtrada) para elegir la
  banda. Con `Aplicar` activado el filtro entra en promedios, waterfall, MASW
  y export; los parámetros persisten en
  `Crudos\Canchita\filter_settings.json`;
- **campañas con fs distinta** (3 s @ 2929 Hz del 3/7 y 10.59 s @ 1020 Hz del
  7/7, ventana larga para ver bajas frecuencias ~1 Hz): dentro de cada grupo
  de distancia las capturas se resamplean (`resample_poly`) a una fs común —
  la mínima del grupo, o la `fs comun` fijada en `Filtros` — y se alinean por
  su trigger. Las capturas viejas de 3 s siguen aportando al promedio en el
  tramo donde tienen datos; la cola larga la definen solo las de 10.59 s (lo
  faltante queda en NaN, no se inventa señal). Nunca se descarta un grupo por
  tener fs mezcladas;
- en la pestaña `Promedios / arrivals`, cuando todos los crudos tengan bien
  su label real de distancia, se recalculan los promedios por label usando el
  trigger del hammer (calculo liviano en memoria, no reescribe nada a disco),
  y se marca manualmente la llegada sobre cada señal promediada. Al cambiar
  de pestaña solo se recalcula si algo relevante cambió (aceptación,
  distancia o trigger de alguna muestra) desde la última vez;
- `Ver waterfall` arma el waterfall y lo manda a la pestaña `Waterfall`, sin
  escribir nada a disco. Esa pestaña es interactiva: mové el mouse para ver
  el cursor (tiempo, distancia+amplitud) bajo el puntero, rueda para zoom y
  arrastrar para paneo. Incluye una traza extra abajo de todo con el
  promedio de **todos** los hammers (siempre en polaridad invertida, sin
  importar cómo estaba marcado cada canal individualmente);
- el checkbox `Amplitud real (ver atenuacion)` de la pestaña `Waterfall`
  cambia el escalado: por defecto cada traza se normaliza a su propio pico
  (mejor para comparar formas), pero con el check activado todas comparten
  la misma escala, así se puede ver como cae la amplitud con la distancia;
- al promediar, si una captura terminó antes que las demás de su mismo label
  (o antes que la más larga de todo el dataset, para el waterfall), esa parte
  faltante queda en blanco (no se rellena con ceros ni se corta todo al más
  corto): la curva simplemente no se dibuja más allá de sus datos reales, en
  vez de aplanarse a cero como si la señal hubiera desaparecido;
- presionar `Exportar waterfall` en la pestaña de promedios para guardar a
  disco el waterfall final con los arrivals marcados, en `.png` y en un
  `.pdf` más alto (una franja por distancia) para revisar el detalle de cada
  traza sin que se amontonen.

### Análisis MASW (pestaña `MASW`)

El botón `Ver MASW` de la pestaña `Waterfall` manda las trazas promedio a la
pestaña `MASW`, que tiene las tres etapas del flujo clásico como
sub-pestañas:

1. **Dispersion**: la imagen frecuencia-velocidad de fase (método
   phase-shift, Park et al. 1998). La banda de frecuencias se elige con
   `f min` / `f max` (el geófono de campo responde de 1 a 200 Hz; los
   defaults arrancan en 1 Hz). La cresta de máxima amplitud es la curva
   de dispersión del modo fundamental Rayleigh. Se marca con `Auto-pick`
   (máximo por frecuencia dentro del rango elegido) y se corrige a mano con
   el checkbox de edición: click izquierdo agrega/mueve un pick, click
   derecho borra el más cercano. Con la curva limpia, `Usar curva →
   Inversion`.
2. **Inversion**: búsqueda Monte Carlo del perfil de capas (Vs y espesores)
   cuya curva de dispersión teórica (fast delta matrix, Buchen & Ben-Hador
   1996) mejor ajusta la curva picada. Mientras itera se ve el loop en vivo,
   como el flujo clásico de MASW: arriba el **Earth (Vs) Model** (mejor
   modelo actual en verde contra el inicial en gris punteado) y abajo la
   **Dispersion Curve** (medida en puntos rojos vs teórica), con el
   desajuste actualizándose. Parámetros: número de capas, iteraciones,
   anchos de búsqueda `bs`/`bh` (%), Poisson y densidad. El modelo inicial
   se genera solo con la regla Vs ≈ 1.09·c(λ=2z).
3. **Perfil Vs**: el **Final Vs Model** — perfil de velocidad de corte vs
   profundidad del mejor modelo (verde) contra el inicial (gris), con el
   detalle de capas y `Guardar resultados (CSV)` (curva de dispersión +
   perfil).

**Modo automático**: el botón `Auto inversion` de la pestaña `Waterfall`
corre las tres etapas sin interacción: calcula la imagen, extrae la curva
con filtros de calidad (umbral de amplitud adaptativo, límites físicos del
tendido — λ ≤ 1.5·apertura y λ ≥ 2·espaciado —, rechazo de outliers contra
mediana móvil) e invierte. Todos los parámetros que eligió quedan visibles
en los controles de la pestaña MASW, para poder revisarlos o repetir el
proceso a mano paso a paso.

Los algoritmos están en `masw_dispersion.py` y `masw_inversion.py`: puertos
a NumPy puro de `third-party/maswavespy` (que requiere compilar extensiones
Cython con numpy<2, no disponible en este entorno), validados contra la
solución analítica de semiespacio homogéneo (c/Vs = 0.919 para ν=0.25) y
con recuperación de modelos sintéticos de 2 capas.

Las marcas se guardan solas al cerrar en:

```bash
C:\Github\Tesis\Crudos\Canchita\field_review_annotations.json
```

También se puede exportar sin abrir la GUI, usando las marcas ya guardadas:

```bash
.\.venv\Scripts\python.exe review_field_data.py --export-only
```

La salida por defecto queda en:

```bash
C:\Github\Tesis\Crudos\Canchita_procesado
```

Contenido principal:

- `muestras/<distancia>/`: cada golpe alineado al trigger, en `.npz`, `.csv` y
  `.json`;
- `promedios/`: promedio por distancia en `.npz`, `.csv` y `.mat`;
- `promedios/hammer_global_promedio.npz` y `.csv`: promedio del hammer de
  **todas** las capturas aceptadas (siempre invertido), usado como referencia
  en el waterfall;
- `average_arrivals.json`: llegadas marcadas manualmente sobre los promedios;
- `waterfall_promedios.png`, `waterfall_promedios.pdf` (version alta, una
  franja por distancia, para ver detalle) y `waterfall_matrix.csv`;
- `duplicados_descartados.json`, con las carpetas repetidas ignoradas.

## Layout de archivos

| Archivo | Función |
|---------|---------|
| `config.py` | Constantes: baud, tipos de paquete, comandos |
| `protocol.py` | Encode/decode de paquetes |
| `serial_worker.py` | QThread para I/O serie |
| `debug_port.py` | QThread para UART debug del esclavo |
| `signal_proc.py` | FIR (`firFilter`, `filtFilt`), `dcRemove`, notch armónico |
| `data_store.py` | Buffers circulares por nodo + stats |
| `logger.py` | Log dual humano/máquina a archivo |
| `zip_to_mat.py` | Conversor ZIP (web UI) → `.mat` |
| `gui/main_window.py` | QMainWindow — integra todos los componentes |
| `gui/stream_tab.py` | Conexión, ARM, START/STOP, guardar |
| `gui/slave_tab.py` | Controles PGA/VDAC/FIR por esclavo |
| `gui/plot_area.py` | Gráficas tiempo real (pyqtgraph) |
| `main.py` | Entry point |

## Protocolo

### PC → Maestro (comandos)

| Formato | Bytes | Comandos |
|---------|-------|---------|
| Estándar | 4 | `0xAB cmd param (cmd^param)` |
| Set-N 16-bit | 5 | `0xAB cmd n_lo n_hi (cmd^n_lo^n_hi)` |
| Dirigido | 6 | `0xAB 0xBD node_id sub_cmd param (node_id^sub_cmd^param)` |

### Maestro → PC (paquetes de 6 bytes)

`[0x56][node_id][type][b2][b1][b0]`

| type | Significado |
|------|-------------|
| `0x00` | Muestra ADC (int24 signed) |
| `0x01` | Heartbeat (PGA, VDAC, master_state) |
| `0x07` | ACK |
| `0xFC` | Latencia START (µs, 24-bit) |
| `0xFD` | Status / HELLO esclavo |
| `0xFE` | READY (n_slaves_ready) |

**Nota:** `Fs` no tiene constante nominal en `config.py` — siempre viene del
HELLO del esclavo (el PSoC reporta 1020 Hz en el firmware actual). La app
la lee de `PTYPE_STATUS` al arrancar.

## Formato de datos guardados (.mat)

```python
from scipy.io import loadmat
d = loadmat("muestra_20260701_143022.mat")
raw_slave1 = d["node1_raw"].ravel()   # float32 array en voltios
fs = float(d["fs"].squeeze())         # 1020.0 (valor real reportado por el PSoC)
fir_cmd = str(d["node1_fir_cmd"])
```

## Comandos FIR (campo `Cmd` de cada esclavo)

```python
lp 200
hp 10
bp 10 400
bs 45 55
numtaps 201 lp 150
firls(73, (0, 1, 2, 3, 4, 5), (0, 0, 1, 1, 0, 0), fs=FS)
remez(73, [0, 40, 45, 55, 60, 510], [1, 0, 1], fs=FS)
firwin(101, [45, 55], pass_zero="bandstop", fs=FS)
b = [0.25, 0.5, 0.25]
```

`FS` y `fs` están disponibles como la tasa de muestreo real del hardware.
