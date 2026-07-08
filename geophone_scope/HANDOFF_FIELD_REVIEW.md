# Handoff — Pulido de review_field_data.py (Capturas / Filtros / Enfase / Waterfall / MASW)

Este documento registra el trabajo en curso sobre la herramienta de revisión de campo
(`python review_field_data.py`, GUI en `field_review_app.py`, modelo/datos en
`field_review_data.py`). Objetivo pedido por el usuario: guiar la calibración empezando
por la señal más fácil de ver (mayor pico a pico), no dar nada por "ok" sin revisión manual,
mostrar en vivo el promedio de lo aprobado, y dar control explícito (no implícito) sobre
inversión de polaridad, inclusión de trazas en el waterfall y picks de MASW.

Si retomás este trabajo (Codex u otra sesión): leé este archivo entero antes de tocar
código, y actualizá la tabla de estado + la sección de la fase correspondiente cuando
termines algo.

## Cómo probar

```bash
cd src/python/geophone_scope
python review_field_data.py --raw-root ..\..\..\Crudos\Canchita
```

(o simplemente `python review_field_data.py`, que ya apunta por default a
`C:\Github\Tesis\Crudos\Canchita`). No hay tests automatizados para esta GUI — la
verificación de cada fase es manual, corriendo la app contra datos reales.

## Estado de las fases

| # | Fase | Estado |
|---|------|--------|
| 0 | Handoff doc + esqueleto | Hecho |
| 1 | Helper compartido de pico a pico | Hecho |
| 2 | Capturas: orden por pico a pico descendente | Hecho |
| 3 | Default "sin validar" + gateo de promedio/waterfall por revisión | Hecho |
| 4 | Invertir señal individual (además de por carpeta) | Hecho |
| 5 | Promedio OK en vivo, color chillón, en Capturas | Hecho |
| 6 | Filtros: diagnosticar y arreglar bug de orden > 4 | Hecho |
| 7 | Enfase: offset por señal + flujo uno-por-uno en orden pico a pico | Hecho |
| 8 | Waterfall: recorte de tiempo + checkboxes por traza | Hecho (vista/MASW; export sin tocar, ver nota) |
| 9 | MASW: picks con botones Añadir/Borrar/Arrastrar | Hecho |
| 10 | MASW: filtro anti-aliasing (c≥2·dx·f) + región M0 para autopick | Hecho |
| 11 | Capturas: simplificación (teclado, quitar botones, auto-guardado, sesión) | Hecho |
| 11b | Enfase: media parcial en vez de todas las señales | Hecho |
| 12 | Waterfall: filtro K direccional f-k (quitar rebotes) | Hecho |

## Hallazgos clave de la investigación previa (antes de tocar código)

- `PickAnnotation.reviewed: bool` (default `False`) YA EXISTE y ya se pone en `True` desde
  `_save_and_next` (`field_review_app.py:1100`, botón "Guardar y siguiente",
  `field_review_app.py:275`). El promedio/waterfall hoy solo miran `accepted` (default
  `True`), no `reviewed` — por eso todo entra al promedio sin revisión explícita. La Fase 3
  soluciona esto gateando por `reviewed and accepted`; no hace falta migrar el JSON porque
  `reviewed=False` ya es el default para todo lo no tocado.
- No existe ninguna métrica de pico a pico en el código (grep vacío). Se agrega en Fase 1
  como helper compartido entre Capturas (Fase 2) y Enfase (Fase 7).
- El "orden 4" de Filtros NO está clampeado en ningún lado: `order_spin` permite 1-10
  (`field_review_app.py:1242-1243`), `design_bandpass_filter` solo pone un piso de 1
  (`field_review_data.py:554`), y la carga de JSON no clampea. El único lugar donde aparece
  "4" es un hint de texto ("orden bajo 2-4"), no una restricción funcional. Diagnóstico
  pendiente en Fase 6 (candidato principal: `filtfilt` con `padlen` > longitud de señal en
  las capturas cortas de 3 s @ 2929 Hz).
- "Picks" (aclarado por el usuario: "picks en masw") se refiere a los picks de la curva de
  dispersión en `MaswPanel.picks: dict[float, float]` (pestaña "1. Dispersion",
  `field_review_app.py:2213-2620`), NO a los picks de trigger/arrival de Capturas ni de
  Promedios. Hoy es un solo checkbox `edit_picks_check` (línea 2316, "izq: agrega, der:
  borra") que puede pisar un pick existente sin querer al clickear cerca. Fase 9 lo
  reemplaza por 3 modos explícitos.
- Enfase (Fase 7): el usuario pidió offset **por señal individual**, no por carpeta (opción
  no recomendada, pero la elegida). Esquema actual: `alignment_offsets.json` es
  `dict[label][folder] = offset_s` (`field_review_data.py:467-518`,
  `get_alignment_offset` en línea 518). Hay que extenderlo a un nivel por-shot con fallback
  al de carpeta, sin romper archivos viejos.

## Notas por fase

### Fase 0 — Handoff doc
Hecho. Este archivo.

### Fase 1 — Helper de pico a pico
Hecho.

- `peak_to_peak(samples: np.ndarray) -> float` en `field_review_data.py` (junto a
  `load_signal`, línea ~294): `max(finite) - min(finite)`, ignora NaN, devuelve 0.0 si
  está vacío/todo-NaN. Función pura, sin Qt, sin dependencia de `PickAnnotation`.
- `FieldReviewWindow._shot_peak_to_peak(shot) -> float` en `field_review_app.py` (junto a
  `_load_pair`): usa `_load_pair(shot)` (mismo cache de señales ya existente) y aplica
  `peak_to_peak` sobre el canal **geo** (no hammer). Cachea en `self._p2p_cache` por
  `shot_id`.
- Importante: el pico a pico es invariante ante `geo_flip` (invertir el signo no cambia
  `max-min`), así que el cache de p2p **no necesita invalidarse** cuando el usuario
  invierte una carpeta (Fase existente) o una señal individual (Fase 4). Esto simplifica
  Fase 2/4/7: no hay que preocuparse por refrescar el orden al tocar el botón de invertir.
- Verificado con datos reales de `Crudos/Canchita` (365 shots): p2p decrece con la
  distancia como se espera físicamente (~0.35-0.40 V a 10-14 m, ~0.04-0.08 V a 46-50 m),
  y es razonablemente consistente dentro de un mismo grupo de distancia.
- Usable desde ambos lados: Capturas (mismo `FieldReviewWindow`, llamada directa) y Enfase
  (`AlignmentPanel`, vía un callback nuevo a pasar en el constructor, igual que ya se hace
  con `get_zeroed` para `_zeroed_pair`) — pendiente de cablear en Fase 2 y Fase 7
  respectivamente.

### Fase 2 — Capturas: orden por pico a pico
Hecho.

- Nuevo combo `self.order_combo` en Capturas (`field_review_app.py`, arriba del combo de
  filtro), con dos modos: `_ORDER_MODE_P2P` = "Pico a pico (mayor primero)" (default) y
  `_ORDER_MODE_ORIGINAL` = "Carpeta / captura (original)".
- La tabla ya NO asume `fila == índice en dataset.shots`. Se agregó una capa de
  indirección: `self._row_order: list[int]` (permutación de índices de `dataset.shots`,
  fila `i` de la tabla muestra `dataset.shots[self._row_order[i]]`) y su inversa
  `self._shot_idx_to_row: dict[int, int]`. `dataset.shots` (el orden canónico usado por
  export/promedios) nunca se toca.
- `_compute_row_order()` calcula la permutación (p2p descendente vía
  `_shot_peak_to_peak`, empate estable por índice original; o identidad si el modo es
  "original"). `_reorder_rows()` la recalcula, repuebla la tabla, y **preserva la
  selección actual por `shot_id`** (no por número de fila) para no perder el lugar al
  cambiar de modo.
- Se corrigieron TODOS los sitios que asumían `fila == índice`: `_populate_table`,
  `_apply_filter`, `_visible_rows`, `_select_row`, `_current`, el refresco de tema
  (`_apply_theme`), `_auto_visible`, y un bug real encontrado en el camino:
  `_apply_distance_to_folder` llamaba `_update_table_row(row, other)` usando el índice de
  `dataset.shots` como si fuera la fila de tabla — ahora usa
  `self._shot_idx_to_row[shot_idx]`. Este último no era nuevo (ya existía antes de este
  cambio, pero con orden idéntico a filas nunca se notaba); quedó documentado por si
  aparece un caso similar en Fase 7 (Enfase también itera todo el dataset por carpeta).
- El orden se computa UNA sola vez por cambio de modo, no en cada redraw: pico a pico es
  invariante ante `geo_flip` (ver Fase 1), así que no hace falta recalcular al invertir
  una señal.
- Verificado con datos reales y `QApplication` real (sin mostrar ventana): orden
  descendente confirmado sobre los primeros 10 valores, selección persiste por
  `shot_id` al cambiar de modo, modo "original" coincide exactamente con el orden
  canónico del dataset, y `_apply_distance_to_folder` (que dispara el path de la
  inversa) corre sin excepción.
- Pendiente para pulir después (no bloqueante): el combo de orden no se guarda en
  ningún JSON, vuelve a "Pico a pico" en cada reinicio — parece la elección correcta
  dado el pedido del usuario (siempre empezar por lo más fácil de ver), pero anotarlo
  acá por si se pide lo contrario.

### Fase 3 — Default sin validar + gateo
Hecho.

- Tabla de Capturas: columnas "Usar"+"Rev" se reemplazaron por una sola columna
  "Estado" (la tabla pasó de 7 a 6 columnas — `field_review_app.py`, `_build_ui` y
  `_update_table_row`). Texto y color vienen de `_estado_display(reviewed, accepted)`:
  - `reviewed=False` → **"Sin validar"** (ámbar) — default de toda marca nueva.
  - `reviewed=True, accepted=True` → **"OK"** (verde).
  - `reviewed=True, accepted=False` → **"Rechazada"** (gris, igual que antes).
- Gateo real en `field_review_data.py`:
  - `compute_average_groups` (línea ~715): ahora exige `ann.accepted and ann.reviewed`
    (antes solo `accepted`). Alimenta Promedios, Waterfall y MASW en la GUI (todos
    consumen `self.averages` de `AverageReviewPanel`, que sale de esta función) — un
    solo cambio propaga a las tres pestañas.
  - `export_processed` (línea ~845 en adelante): la exportación INDIVIDUAL de cada
    captura (npz/csv/json bajo `muestras/<label>/`) sigue gateada solo por `accepted`
    (sin cambios) — se sigue exportando el crudo de todo lo aceptado, revisado o no.
    Pero la inclusión en `grouped`/`all_hammer_items` (lo que alimenta promedios,
    waterfall y manifest) ahora requiere además `ann.reviewed` — ver el `continue`
    agregado justo después de escribir `sample_meta`. Decisión explícita: separar
    "¿se guarda el archivo crudo?" de "¿entra al promedio?" para no romper el export
    batch de muestras individuales (`--export-only` en runs sin GUI).
  - `annotations_signature` (línea ~687): se le agregó `bool(ann.reviewed)` a la tupla.
    Sin esto, el cache de "no cambió nada, no recalcules" de `AverageReviewPanel`
    (`_last_signature`) NO se hubiera invalidado al marcar una señal como revisada —
    bug silencioso que se hubiera notado como "marqué OK pero el promedio no se
    actualiza". Encontrado y corregido en este mismo paso, no hacía falta tocarlo antes
    porque nada dependía de `reviewed` todavía.
- **CAMBIO DE COMPORTAMIENTO IMPORTANTE, INTENCIONAL Y VERIFICADO**: con las
  anotaciones reales en disco (`Crudos/Canchita/field_review_annotations.json`,
  365 shots, 353 `accepted`, **0 `reviewed`** porque nunca se usó ese campo para
  gatear nada hasta ahora), `compute_average_groups` devuelve **0 grupos** hoy mismo.
  Promedios/Waterfall/MASW van a verse vacíos la próxima vez que se abra la app,
  hasta que el usuario empiece a revisar señal por señal (con "Guardar y siguiente"
  o el futuro botón "marcar OK" de Fase 5). Esto es exactamente lo que pidió el
  usuario ("ahora todos debes poner así sin validar"), no es un bug ni hace falta
  migrar el JSON — pero avisale la primera vez que abra la app después de este
  cambio para que no piense que se perdió el trabajo previo (sigue todo en el JSON,
  con `reviewed=False`).
- Verificado con datos reales: `_estado_display` en las primeras filas muestra
  "Sin validar" (confirmado por script), `compute_average_groups` con las
  anotaciones reales da 0 grupos, y forzando `reviewed=True` en un solo shot (en una
  copia en memoria, sin tocar el JSON en disco) aparece 1 grupo con `n=1`.
- Pendiente relacionado, no de esta fase: `AlignmentPanel._shots_for_label` (Enfase)
  y `AverageReviewPanel`/`refresh()` de labels todavía filtran solo por `accepted`,
  no por `reviewed` — se van a redefinir en Fase 7 como parte del flujo de
  "acumulado de OK", así que no se tocaron acá para no duplicar trabajo.

### Fase 4 — Invertir individual
Hecho.

- Nuevo botón `self.flip_geo_single_btn` ("Invertir esta señal") en el grid de nav de
  Capturas (`field_review_app.py`, fila 4, debajo de "Invertir geo de carpeta" que
  quedó en la fila 3). Handler `_flip_geo_single()`, junto a `_flip_geo_folder`: hace
  `ann.geo_flip = not ann.geo_flip` + `ann.source = "manual"` SOLO para el shot actual
  (sin el loop por carpeta).
- No hace falta invalidar ningún cache de pico a pico (Fase 1: p2p es invariante ante
  el signo).
- Verificado con datos reales: togglear una señal individual solo cambia su propio
  `geo_flip`, ninguno de sus 10 hermanos de carpeta se mueve; togglear de nuevo
  restaura el estado original.

### Fase 5 — Promedio OK en vivo
Hecho.

- Se promovió `field_review_data._segment_nan_padded` a pública
  (`segment_nan_padded`, sin guion bajo) — utilidad chica y ya probada para armar
  segmentos alineados con NaN fuera de rango, reusada ahora también desde
  `field_review_app.py` en vez de duplicar la lógica. Todos los call sites internos
  de `field_review_data.py` (5 usos: `compute_average_groups`, `export_processed`,
  `_export_waterfall`/similares) se renombraron con el mismo cambio, sin tocar
  comportamiento.
- `FieldReviewWindow._ok_average_for_distance(distance_m)` (junto a
  `_plot_overlays`): versión liviana de `compute_average_groups` pero:
  - Solo mira el grupo de distancia del shot actual (no recalcula todo el dataset
    en cada redraw).
  - Reusa `_zeroed_pair`/`_load_pair` (cache de señales ya en memoria) en vez de
    releer archivos del disco.
  - NO resamplea entre fs distintas dentro del mismo grupo (si aparece un fs
    distinto al de la primera señal OK encontrada, esa señal se ignora en este
    preview en vivo). El promedio "real" para exportar/waterfall/MASW sigue siendo
    `compute_average_groups`, que sí mezcla fs (ver nota de campañas 2929/1020 Hz
    en memoria del proyecto). Documentado como limitación conocida, aceptable
    porque es solo una vista previa de calibración.
  - Alinea por índice de trigger igual que `compute_average_groups` (mismo patrón
    `rel_start`/`rel_end`/`segment_nan_padded`), gateado por `accepted and
    reviewed` (mismo criterio "OK" de Fase 3).
- `FieldReviewWindow._plot_ok_average(ann)`: dibuja el resultado en `self.geo_plot`
  con `self._OK_AVERAGE_COLOR = "#ff33cc"` (magenta chillón), ancho 3, por encima
  del resto (`setZValue(30)`). Se llama desde `_refresh_plot()` justo después de
  `_plot_overlays`.
- Se agregó `self._refresh_plot()` al final de `_accepted_changed` (antes no
  refrescaba el plot al tocar el checkbox "Usar esta muestra") para que marcar/
  desmarcar una señal actualice el promedio en vivo inmediatamente. La navegación
  normal (`_select_row`, `_save_and_next`, `_reset_auto`, etc.) ya llamaba
  `_refresh_plot()` así que no hizo falta tocar esos.
- Verificado con datos reales: sin señales OK en un grupo, `_ok_average_for
  _distance` devuelve `None` (no dibuja nada); marcando 3 señales de 10 m como
  `accepted=True, reviewed=True` (en memoria, sin guardar a disco) aparece un
  promedio de 8790 muestras sin NaN dentro del tramo común; `_refresh_plot()`
  corre sin excepción y el trazo con el color `#ff33cc` efectivamente queda en
  `geo_plot.listDataItems()`.

### Fase 6 — Bug orden filtro > 4
Hecho. **Causa raíz real (no era un cap hardcodeado, como se sospechaba en la
investigación inicial):**

- `design_bandpass_filter`/`apply_bandpass_filter` en `field_review_data.py` usaban
  `scipy.signal.butter(...)` en forma de coeficientes `(b, a)` + `filtfilt`. Con un
  corte bajo cerca de 1 Hz y `fs` de cientos/miles de Hz, la banda relativa
  (`Wn = 1 Hz / Nyquist`) es muy angosta, y la forma `(b, a)` de un Butterworth se
  vuelve numéricamente **inestable a partir de orden 6**: los coeficientes crecen
  varios órdenes de magnitud y `filtfilt` devuelve `inf`/`NaN` **en silencio**, sin
  excepción ni aviso (solo un `RuntimeWarning` de numpy al castear a float32 que no
  llega a la UI). El usuario ve el preview "roto" (plano/vacío) a partir de orden 6
  y lo interpreta como "no me deja pasar de 4".
- **Reproducido y confirmado con datos reales de las dos campañas** (2929 Hz y
  1020 Hz, corte 1-80 Hz): orden 2 y 4 dan salida finita; orden 6 ya da `inf`;
  orden 8 y 10 dan `NaN`. Coeficientes `(b,a)` pasan de máximo ~55 (orden 4) a
  ~660 (orden 6) a ~100000 (orden 10) — la firma clásica de este problema conocido
  de scipy/Butterworth.
- **Fix**: `design_bandpass_filter` ahora devuelve `sos` (second-order sections,
  `butter(..., output="sos")`) en vez de `(b, a)`, y `apply_bandpass_filter` usa
  `scipy.signal.sosfiltfilt` en vez de `filtfilt`. SOS es la forma que scipy
  recomienda exactamente para este caso (bajo orden alto + banda angosta) y se
  mantiene estable en todo el rango 1-10 que ya permitía el spinbox
  (`order_spin.setRange(1, 10)`, sin cambios ahí). El guard de "señal muy corta
  para el padding" se actualizó a `3 * (2*sos.shape[0] + 1)` (equivalente al
  viejo `3*max(len(a),len(b))` pero en términos de secciones SOS).
- Import actualizado: `from scipy.signal import butter, resample_poly, sosfiltfilt`
  (se sacó `filtfilt`, ya no se usa en ningún lado del módulo).
- Se ajustó el hint de la pestaña Filtros (`field_review_app.py`, `FilterPanel`):
  ya no dice solo "conviene orden bajo (2-4)" sin contexto; ahora aclara que
  5-10 aplican bien (antes eran inestables) y que la recomendación de orden bajo
  es por el mayor transitorio al inicio de la señal, no porque orden alto esté roto.
- Verificado: `apply_bandpass_filter` en órdenes 1,2,4,6,8,10 sobre señales reales
  de AMBAS campañas da salida 100% finita; el guard de señal corta sigue
  devolviendo la señal sin tocar cuando corresponde; y un smoke test end-to-end de
  `FilterPanel` (UI real, `enable_check` + spins + `refresh_preview()`) corre sin
  excepción en órdenes 4/6/8/10.
- No se tocó `filter_settings.json` ni su esquema — mismo campo `order: int`, mismo
  rango de spinbox, solo cambió CÓMO se diseña/aplica el filtro internamente.

### Fase 7 — Enfase por señal
Hecho. Fue la fase más grande; quedó una nota aparte más abajo con un incidente de
testing que hay que tener en cuenta a futuro.

**Esquema nuevo (archivo NUEVO, no se tocó el formato de `alignment_offsets.json`):**
- `alignment_shot_offsets.json` (nuevo, junto a `alignment_offsets.json` en
  `Crudos/Canchita/`): `{"schema": ..., "updated_at": ..., "shot_offsets": [{"shot_id":
  ..., "offset_s": ...}]}`. Funciones en `field_review_data.py`:
  `default_alignment_shot_offsets_path`, `load_alignment_shot_offsets`,
  `save_alignment_shot_offsets`, `alignment_shot_offsets_signature` (mismo patrón que
  las funciones equivalentes de `alignment_offsets.json`, solo que planas por
  `shot_id` en vez de anidadas por label/carpeta — no hizo falta anidar porque
  `shot_id` ya es único globalmente).
- `get_alignment_offset(offsets, distance_m, folder_name, shot_id=None,
  shot_offsets=None)`: si `shot_id` está en `shot_offsets`, ese valor GANA; si no,
  cae al offset de carpeta de siempre (`offsets[label][folder]`); si tampoco hay,
  `0.0`. Esto es lo que hace que "una señal nunca ajustada a mano siga usando el
  offset de su carpeta" sin ningún paso de migración.
- Todos los consumidores actualizados para pasar `shot_id=shot.shot_id,
  shot_offsets=alignment_shot_offsets`: `compute_average_groups`, `export_processed`
  (data), y sus llamadores en la GUI (`AverageReviewPanel._refresh_averages`,
  `AverageReviewPanel._export_waterfall`, `FieldReviewWindow._export`) y en el CLI
  (`review_field_data.py --export-only`).
- `annotations_signature`... digo, acá es `alignment_shot_offsets_signature`: se
  agregó a la tupla de `_refresh_averages`'s `_last_signature` en
  `AverageReviewPanel` (mismo motivo que en Fase 3 con `reviewed`: sin esto, ajustar
  un offset por señal no hubiera disparado un recalculo del promedio en pantalla).
- `manifest.json` de export ahora incluye una lista `alignment_shot_offsets` (mismo
  formato que la lista `alignment_offsets` ya existente).
- `FieldReviewWindow._alignment_offsets_changed` (el callback `on_changed` que le
  pasa a `AlignmentPanel`) ahora guarda AMBOS archivos (`save_alignment_offsets` +
  `save_alignment_shot_offsets`) cada vez que cambia cualquiera de los dos, para no
  necesitar dos callbacks separados. `closeEvent` también guarda ambos.

**Rediseño de `AlignmentPanel` (`field_review_app.py`), de tabla-por-carpeta a
navegación por señal:**
- Constructor ahora recibe `shot_offsets` (dict compartido, igual que `offsets`) y
  `get_peak_to_peak` (callback = `FieldReviewWindow._shot_peak_to_peak`, el mismo
  helper de Fase 1, reusado tal cual entre Capturas y Enfase).
- Se borró la `QTableWidget` de carpetas; ahora hay: label combo, label de texto con
  "posición/total — carpeta/captura — dist — estado — N/total acumuladas", botones
  Anterior/Siguiente (navegan sin persistir nada, solo para previsualizar), un
  `offset_spin` (ms) que junto con el pick de señal actual arranca en el offset
  efectivo de esa señal (`_shot_default_offset_s`: shot-level si existe, si no
  folder-level, si no 0), botón **"OK alineado"** (persiste el offset actual en
  `shot_offsets[shot_id]`, dispara `on_changed`, avanza a la siguiente — mismo
  patrón que "Guardar y siguiente" de Capturas), **"Reset esta señal"** (saca la
  entrada de `shot_offsets`, vuelve a fallback de carpeta) y **"Reset todo el
  label"** (limpia el offset de carpeta del label Y todos los shot_offsets de las
  señales de ese label).
- `_ordered_shots_for_label(label)`: mismo filtro `accepted` que antes
  (`_shots_for_label` vieja), pero ahora devuelve una lista PLANA (no agrupada por
  carpeta) ordenada por pico a pico descendente vía `get_peak_to_peak`.
- `_redraw()`: dibuja el ACUMULADO (señales con entrada en `shot_offsets`, un color
  de `_ALIGN_COLORS` por señal, excluye la señal actual de este grupo) + la señal
  actual resaltada aparte (blanco en modo oscuro, negro en modo claro — elegido así,
  no un color fijo, porque blanco sobre fondo claro hubiera sido invisible).
  Antes de la primera confirmación de un label no hay nada acumulado: solo se ve la
  señal actual, que es exactamente el comportamiento pedido ("empezamos con el que
  tiene mayor pico a pico y vamos viendo el acumulado a medida que confirmamos").
- `set_dark_mode`/`_apply_theme` actualizados al nuevo estado (llaman
  `_show_current()` en vez de `_label_changed()`).

**INCIDENTE DE TESTING A TENER EN CUENTA (importante para quien siga)**: al
verificar esto por primera vez con `FieldReviewWindow` real apuntando a
`Crudos/Canchita`, `_mark_ok()` disparó `on_changed` → `save_alignment_shot_offsets`
y escribió un `alignment_shot_offsets.json` REAL en el repo con un valor de prueba
(12.5 ms en un shot al azar). Se detectó con `git status`/inspección del archivo y
se borró antes de seguir. **Para cualquier test futuro que instancie
`FieldReviewWindow` sobre el `raw_root` real y ejercite botones que llaman
`on_changed` (marcar OK, resets, cambios de offset, `_accepted_changed`, etc.),
hay que noquear `save_*` primero** (`field_review_app.save_alignment_offsets = lambda
*a, **k: None`, ídem `save_alignment_shot_offsets`/`save_annotations`/
`save_filter_settings`) para no pisar datos reales del usuario. Quedó así
resuelto en los tests que corrí, pero no hay ninguna protección en el código
mismo — es un riesgo puramente de testing, no un bug de la app.
- Verificado (con saves noqueados): orden por pico a pico descendente en Enfase;
  0 acumuladas al empezar un label; `OK alineado` persiste el offset por shot_id y
  avanza; una señal nunca tocada cae al offset de carpeta y seguía el valor real
  que ya había en `alignment_offsets.json` (50 ms en el label/carpeta de prueba);
  el fallback seguía un cambio de offset de carpeta en vivo; `Reset esta señal` y
  `Reset todo el label` limpian lo que corresponde; `_redraw()` corre sin excepción
  con 1 acumulada + 1 actual; `compute_average_groups` acepta y usa
  `alignment_shot_offsets` sin tirar excepción.
- Pendiente de pulir (no bloqueante): no hay indicador visual en el `label_combo` de
  cuántos labels ya tienen alineación completa; y `Reset todo el label` borra
  TAMBIÉN el offset de carpeta legado del label (antes solo tocaba shot_offsets),
  decisión tomada para que "reset" sea realmente un reset completo — si se prefiere
  que el reset NO toque el offset de carpeta legado, es un cambio de una línea
  (sacar el `if label in self.offsets: self.offsets[label] = {}` de `_reset_label`).

### Fase 8 — Waterfall recorte + checkboxes
Hecho para la VISTA interactiva y para lo que se manda a MASW. **Alcance
recortado a propósito, documentado acá**: el export (CSV/PNG/PDF desde la pestaña
Promedios, `export_processed`/`_export_waterfall` en `field_review_data.py`) NO
respeta este recorte/filtro — sigue exportando todas las distancias y el rango
completo, como antes. Nadie lo pidió explícitamente así, fue una decisión de
alcance para no tener que hacer viajar el estado de recorte/filtro desde
`WaterfallPanel` (vive en `field_review_app.py`, es puramente de UI) hasta
`AverageReviewPanel.export_processed(...)`/`_export_waterfall` (que arma el CSV
real). Si el usuario efectivamente quiere que el recorte/filtro también afecte el
archivo exportado, es la próxima tarea natural sobre esta fase: pasar
`hidden_distances`/`trim_range` como parámetros nuevos de `export_processed` /
`_export_waterfall`, filtrando `averages` antes de armar `waterfall_matrix.csv`.

**Lo implementado** (`field_review_app.py`, clase `WaterfallPanel`):
- Fila de recorte: checkbox "Recortar tiempo" (default off = comportamiento
  idéntico a antes) + dos `QDoubleSpinBox` ("desde"/"hasta", en segundos).
  `_trimmed_time_and_matrix(common_time, matrix)` recorta por índice
  (`np.searchsorted`) ambos arrays cuando el checkbox está activo y el rango es
  válido (`end > start`); si no, devuelve los arrays sin tocar.
- Lista de trazas: `QListWidget` con un ítem checkeable por distancia (poblada en
  `_rebuild_trace_list`, llamada desde `populate()`), mas botones "Todas"/"Ninguna".
  El estado de qué distancias están destildadas vive en `self._hidden_distances`
  (set de distancias redondeadas a 6 decimales, para evitar problemas de float).
  `_visible_distances_and_matrix` filtra ambas listas en paralelo.
- `_redraw()` y `_emit_masw()` (los dos consumidores de `_last_data`) aplican
  PRIMERO el recorte de tiempo y DESPUÉS el filtro de distancias visibles, así
  que "Ver MASW"/"Auto inversion" heredan exactamente lo que se está mirando en
  pantalla. Si el resultado queda vacío (todo oculto, o rango de recorte inválido/
  vacío), `_redraw` muestra un mensaje claro en vez de crashear, y `_emit_masw`
  tira un `QMessageBox.warning` en vez de mandarle arrays vacíos a MASW.
- Al repoblar (`populate()`, se llama cada vez que se recalculan promedios), las
  distancias ocultas que ya no existen en el nuevo dataset se limpian solas del
  set (`self._hidden_distances &= valid_keys`) para no acumular basura.
- Nuevos imports usados: `QListWidget`, `QListWidgetItem` (agregados al import de
  `PyQt6.QtWidgets`).
- Verificado con datos sintéticos (sin necesidad de datos reales, esto es puramente
  de UI/plot): ocultar una distancia la saca tanto del redraw como del handoff a
  MASW; recortar tiempo (0.5-1.0 s sobre un eje 0-2 s) devuelve el rango correcto
  con el tamaño de arrays esperado; combinar recorte + ocultar una traza no
  excepciona; ocultar TODAS las trazas muestra un mensaje en vez de romper.

### Fase 9 — MASW picks Añadir/Borrar/Arrastrar
Hecho (`field_review_app.py`, clase `MaswPanel`, pestaña "1. Dispersion").

- Se eliminó el checkbox único `edit_picks_check` ("izq: agrega, der: borra") que
  podía pisar un pick sin querer. En su lugar, una fila de 4 botones mutuamente
  excluyentes (`QButtonGroup`, todos `checkable`): **"Ver (sin editar)"** (default),
  **"Añadir"**, **"Borrar"**, **"Arrastrar"**. El modo activo vive en
  `self._pick_mode` ("ver"/"anadir"/"borrar"/"arrastrar").
- **Añadir** (`_on_mouse_clicked`, rama `anadir`): click agrega un pick en el bin de
  frecuencia más cercano SOLO si ese bin no tiene ya un pick (si lo tiene, avisa y no
  lo pisa — esto era el bug del checkbox viejo).
- **Borrar** (`_on_mouse_clicked`, rama `borrar`): click elimina el pick más cercano
  (reemplaza el click-derecho de antes).
- **Arrastrar**: se hace overriding de `self._plot_item.vb.mouseDragEvent` con
  `_vb_mouse_drag_event` (se guardó el original en `self._default_drag_event`). En
  modo "arrastrar", si el drag ARRANCA cerca de un pick existente (tolerancia =
  max(4% del rango de f, un bin), medida en frecuencia), lo "agarra"
  (`self._dragging_pick`) y mueve solo su velocidad (la frecuencia del pick no
  cambia); soltar lo libera. Si el drag no arranca cerca de ningún pick, o el modo
  no es "arrastrar", delega al pan normal de pyqtgraph (`_default_drag_event`) — así
  no se rompe el zoom/pan de la imagen.
- "Auto-pick" y "Limpiar picks" quedan igual.
- Verificado con datos sintéticos + `QApplication` real: añadir en bin vacío
  funciona y en bin ocupado avisa sin pisar; borrar saca el más cercano; arrastrar
  mueve la velocidad conservando la frecuencia y libera al soltar; drag lejos de
  todo pick o en otro modo delega al pan sin agarrar nada.

### Fase 11 — Simplificación de Capturas (teclado, botones, auto-guardado, sesión)
Hecho. Pedido del usuario en varios mensajes: manejar todo por teclado, quitar botones
que no aportan, guardar TODO solo (sin botón), y al reabrir continuar donde quedó.

**Teclado** (`FieldReviewWindow`, event filter a nivel app `eventFilter` →
`_handle_review_key`, reemplaza el viejo `keyPressEvent` de ←/→ + `_install_trigger
_shortcuts`/`_run_trigger_shortcut`, ambos eliminados). Esquema (elegido por el usuario
vía AskUserQuestion, opción "zona auto"):
- **W / S** → muestra anterior / siguiente (`_move_row`).
- **A / D** → borde IZQUIERDO de la zona auto − / + (`_nudge_auto_zone("left", ±1)`).
- **← / →** → borde DERECHO de la zona auto − / + (`_nudge_auto_zone("right", ±1)`).
  Si todavía no hay zona, la primera pulsación la crea alrededor del trigger actual
  (±0.05 s). Paso = `_ZONE_NUDGE_SAMPLES` (3 muestras). El trigger en sí se sigue
  moviendo arrastrando la línea naranja con el mouse (NO por teclado — decisión del
  usuario). Nota: la zona auto se limpia sola al cambiar de muestra (comportamiento
  preexistente de `_select_row`).
- **↑ / ↓** → zoom in / out del gráfico HAMMER (`_zoom_plot`, `viewBox.scaleBy` 0.8/1.25).
- **Shift+↑ / ↓** → zoom in / out del gráfico GEO.
- **Espacio** → rota el estado: sin validar → OK → rechazada → sin validar
  (`_cycle_estado`). Desde el default (sin validar) el primer Espacio marca OK.
- **X** → invierte la señal actual (`_flip_geo_single`).
- El event filter solo actúa en la pestaña Capturas, con la ventana activa
  (`isActiveWindow`, así un QMessageBox/diálogo no se come las teclas) y sin foco en un
  campo de texto/spin (así se puede tipear notas y editar valores). Usa event filter (no
  QShortcut) a propósito: para ganarle al manejo nativo de flechas de la tabla/plots.

**Botones quitados** del grid de Capturas (y sus métodos muertos `_auto_visible`,
`_export`, `_open_average_review` eliminados): "Auto visibles" (reescribía marcas, el
usuario lo veía peligroso), "Guardar marcas" (ya no hace falta, auto-guardado),
"Exportar" (el export completo sigue en la pestaña Promedios/arrivals vía
`AverageReviewPanel._export_waterfall`, que llama `export_processed`) y "Ir a
promedios / arrivals" (redundante, se cambia de pestaña con el mouse). Se mantienen:
Anterior/Siguiente, Auto, Guardar y siguiente, Marcar/Limpiar zona auto, Aplicar dist.
a carpeta, Invertir geo de carpeta, Invertir esta señal. La tabla ahora tiene 6 columnas
(la de estado unificada de la Fase 3).

**Auto-guardado** (`_autosave_annotations`, quiet, sin status ni excepciones): se llama
tras CUALQUIER mutación — `_accepted_changed`, `_distance_changed`, `_notes_changed`,
`_apply_distance_to_folder`, `_flip_geo_folder`, `_flip_geo_single`, `_reset_auto`,
`_cycle_estado`, `_trigger_line_change_finished` (fin de drag), `_nudge_trigger`. El
"Guardar y siguiente" y el `closeEvent` también persisten. Nota de rendimiento: el
trigger guarda solo al SOLTAR el drag (`sigPositionChangeFinished`), no en cada pixel
del arrastre (`sigPositionChanged`), para no escribir el JSON cientos de veces.

**Sesión / continuar donde quedó** (nuevo `field_review_session.json` en raw_root;
funciones `default_session_path`/`load_session`/`save_session` en `field_review_data.py`).
Guarda `{last_shot_id, order_mode, filter_mode, filter_distance, dark_mode}`. Se escribe
en `_save_session` (llamado desde `_select_row`, `_order_mode_changed`, `_toggle_theme`,
`closeEvent`; gateado por `_ui_ready` para no guardar durante el arranque). Al abrir,
`__init__` carga la sesión, aplica `dark_mode` antes de construir la UI,
`_restore_session_ui` reaplica orden/filtro (con señales bloqueadas), y
`_session_target_row` selecciona la muestra donde se dejó (o la primera visible si esa
quedó oculta/no existe). Verificado: reabrir cae en el mismo shot y restaura el modo de
orden.

- Verificado con datos reales + QApplication real: W/S navegan; A/D mueven solo el borde
  izquierdo y ←/→ solo el derecho de la zona (creándola si no existe); ↑/↓ hacen zoom del
  hammer y Shift+↑/↓ del geo (sin tocar el otro); Espacio rota los 3 estados; X invierte;
  el foco en un campo de texto bloquea los atajos; auto-guardado dispara en
  accepted/distancia/invert; y al "reabrir" (segunda ventana con la sesión guardada) cae
  en el último shot y restaura el orden. Smoke test de las 6 pestañas OK.
- Pendientes anotados (no bloqueantes): (a) mover un borde de la zona NO re-corre la
  detección de trigger en vivo — solo mueve la caja visual; el usuario aprieta "Auto"
  después. Si se quiere re-detección en vivo al mover el borde, es un cambio chico en
  `_nudge_auto_zone`. (b) A/D y ←/→ usan un paso fijo de 3 muestras; si se quiere un
  Shift=paso grueso para la zona, Shift+A/D y Shift+←/→ están libres. (c) `_nudge_trigger`
  /`_nudge_trigger_by_samples` quedaron sin uso (el trigger ahora es solo mouse) pero se
  dejaron por si se reusan.

### Fase 12 — Waterfall: filtro K direccional f-k (quitar rebotes)
Pedido del usuario: checkbox en Waterfall que aplique un filtro en número de onda K
para separar ondas por dirección y eliminar rebotes/reflexiones.

**Física / algoritmo** (`fk_directional_filter` en `field_review_data.py`, función pura,
junto a `resample_signal`): el waterfall es un gather x-t (una fila por distancia/posición
de geófono, una columna por tiempo). Las ondas que van de la fuente hacia los geófonos
(moveout positivo: llegan más tarde a mayor distancia) y las que rebotan y vuelven
(moveout negativo) caen en mitades OPUESTAS del plano frecuencia-número de onda (f-k).
Con `np.fft.fft2`, una onda forward `s(t − x/c)` (c>0) concentra energía donde `k·f < 0`
(k y f de signo opuesto). El filtro conserva esa mitad (`k·f ≤ 0`, incluyendo los ejes
k=0/f=0 que son no-direccionales) y pone a cero la otra → quedan solo las ondas
progradantes, se van los rebotes. La reconstrucción es real porque la simetría hermitiana
se preserva (un punto (k,f) y su conjugado (−k,−f) tienen el mismo signo de k·f, así que
se conservan o eliminan juntos).

- Maneja **distancias no uniformes**: remuestrea a una grilla espacial uniforme
  (dx = mediana de las separaciones) con `np.interp` por columna de tiempo, filtra, y
  vuelve a las distancias originales. Los **NaN** (colas de trazas más cortas) se rellenan
  con 0 para la FFT y se restauran en la salida. Devuelve la matriz sin tocar si es
  demasiado chica (<3 distancias o <4 tiempos).
- Convención verificada con onda sintética ida+vuelta: forward conservado (corr 0.976),
  backward/reflexión eliminado (corr 0.024), energía forward domina 40:1. `keep_forward
  =False` hace lo opuesto (por si alguna vez se quiere ver solo los rebotes).

**UI** (`WaterfallPanel`): checkbox `self.kfilter_check` ("Filtro K (quitar rebotes)")
al lado de "Amplitud real". Cuando está tildado, `_apply_kfilter(common_time, matrix)`
aplica el filtro sobre TODAS las distancias (antes de descartar las ocultas, para que la
resolución en k use el tendido completo), después del recorte de tiempo. Se aplica tanto
en `_redraw` (la vista) como en `_emit_masw` (lo que se manda a "Ver MASW"/"Auto
inversión"). El export CSV/PNG/PDF de la pestaña Promedios NO se toca (mismo criterio de
alcance que el recorte/checkboxes de la Fase 8).

- Verificado con datos sintéticos + QApplication real: checkbox OFF = identidad; ON quita
  la reflexión (corr forward 0.976 / backward 0.024) tanto en la vista como en la matriz
  que recibe MASW; `_redraw` corre sin excepción; smoke test de las 6 pestañas OK.
- Pendiente anotado (no bloqueante): si alguna vez se quiere el filtro también en el
  archivo exportado, hay que pasar el estado del checkbox a `export_processed`/
  `_export_waterfall` (mismo pendiente que el recorte de Fase 8).

### Fase 11b — Enfase: media parcial en vez de todas las señales
Refinamiento pedido tras la Fase 7. El `_redraw` de `AlignmentPanel` dibujaba UNA traza
por cada señal ya alineada (hasta 30 colores = ruido visual). Ahora dibuja **una sola
media parcial** (verde, `_MEAN_COLOR = "#2ca02c"`, ancho 3) de todas las señales ya
alineadas del label, más la **señal actual** resaltada (blanco/negro según tema) para
moverla con el offset contra esa media.

- Nuevo `AlignmentPanel._partial_mean(exclude_shot_id)`: junta las señales acumuladas
  (las que tienen entrada en `shot_offsets`), corre cada una por su trigger + su offset,
  las interpola a una grilla de tiempo común (maneja fs mezcladas 2929/1020 sin drama,
  NaN fuera del tramo de cada una) y hace `nanmean`. Excluye la señal actual (para que no
  se promedie consigo misma mientras la estás moviendo). Devuelve None si todavía no hay
  ninguna acumulada (primera señal del label → solo se ve la actual).
- `_offset_changed` sigue disparando `_redraw`, así que al mover el spin la señal actual
  se redibuja en vivo contra la media (que queda fija). Al "OK alineado", la actual entra
  al `shot_offsets` y pasa a formar parte de la media parcial para la siguiente.
- Verificado con datos reales: 0 acumuladas → 1 traza (solo la actual, sin media); 5
  acumuladas + actual → exactamente 2 trazas (media + actual), NO 5 individuales; mover
  el offset mantiene 2 trazas; una señal acumulada que se vuelve a visitar se excluye de
  su propia media (n baja de 5 a 4).

### Fase 10 — MASW anti-aliasing (c ≥ 2·dx·f) + región M0 (pedido extra del usuario)
Hecho. Pedido textual: "Vs/f ≥ 2·Δx ... poneme un filtro de esquina y ni grafiques
la zona con aliasing" + "dejame marcar una región donde yo veo que está el modo M0
para delimitar mejor el autopicking".

**Física.** Criterio de Nyquist espacial del tendido: la mínima longitud de onda
resoluble es 2·Δx (Δx = espaciado entre geófonos). Como λ = c/f, la condición de
validez es **c ≥ 2·Δx·f**. La zona de aliasing (a ocultar y a excluir del pick) es
la esquina de baja velocidad / alta frecuencia donde c < 2·Δx·f.

**Espaciado Δx.** `MaswPanel._estimate_spacing_m(distances)` = mediana de las
diferencias entre distancias consecutivas ordenadas (las distancias que llegan al
waterfall SON las posiciones de los geófonos). Se calcula en `set_data` y queda en
`self._geophone_spacing_m`. Si hay menos de 2 distancias distintas, es None y el
filtro anti-aliasing NO se aplica (se avisa en el label).

**No graficar la zona aliasada.** En `_calculate`, además de la imagen normal, se
arma `A_display` = copia de A con `A_display[c < 2·dx·f] = NaN` (por fila de
frecuencia) y ESA es la que se pasa a `image_view.setImage`. El array completo A se
guarda en `_last_result` para que la matemática del pick tenga la grilla entera. Se
dibuja además la línea de borde `c = 2·dx·f` (`_draw_alias_boundary`, línea roja
punteada, recortada al rango de velocidad de la imagen) para que se vea el corte de
un vistazo aunque el render de NaN dependa de la versión de pyqtgraph. Verificado:
~23% de la imagen sintética de prueba queda enmascarada, celdas claramente aliasadas
son NaN y las válidas quedan finitas.

**Filtro en el picking.** `_valid_velocity_mask(f_val, c)` devuelve una máscara
booleana sobre el array de velocidades: True donde `c ≥ 2·dx·f_val` Y (si hay región
M0) `c` cae en el rango de velocidad de la M0. `_auto_pick` ahora, por cada
frecuencia, restringe el `argmax` a esa máscara (`np.where(vmask, A[i], -inf)`) en
vez de tomar el máximo de toda la columna, saltea las frecuencias sin ningún bin
válido, y reporta cuántas descartó por aliasing. El modo "Añadir" también rechaza un
click cuyo `c < 2·dx·f` (avisa el mínimo válido). Verificado: ningún pick del
auto-pick viola c ≥ 2·dx·f; añadir en zona aliasada se rechaza, en zona válida se
acepta.

**Región M0.** Botones "Marcar región M0" / "Quitar región M0". `_add_m0_region`
agrega un `pg.RectROI` amarillo, movible y redimensionable, con posición inicial
razonable (banda de picking en f, tercio central en c); el usuario lo arrastra para
encerrar el modo fundamental. `_m0_bounds()` devuelve (f_min,f_max,c_min,c_max) del
ROI. Mientras exista, `_auto_pick` y "Añadir" quedan restringidos a esa caja (además
del anti-aliasing). `_clear_m0_region` lo saca (también se limpia en `set_data` al
cargar datos nuevos). Verificado: con un ROI de f=10-40 Hz, c=120-250 m/s, todos los
picks del auto-pick caen dentro.

**Alcance / pendientes anotados:**
- El anti-aliasing y la M0 aplican al "Auto-pick" interactivo y al "Añadir" manual.
  El flujo totalmente automático `run_auto` (botón "Auto inversion" del waterfall)
  usa `auto_extract_dispersion_curve` de `masw_dispersion.py`, que NO recibió estos
  filtros — ahí no hay interacción del usuario para marcar M0, y el anti-aliasing
  sería un cambio dentro de ese módulo aparte. Si se quiere, es la próxima tarea:
  pasar `dx` a `auto_extract_dispersion_curve` y restringir su búsqueda a
  c ≥ 2·dx·f.
- La región M0 no se persiste (se pierde al cambiar de dataset o cerrar) — es una
  ayuda visual de sesión, no un dato a guardar. Si se quisiera recordar, habría que
  serializarla aparte.

---

## Fase 9 — Curva editable en inversión, M0 → N regiones multi-modo, persistencia y evodcinv (2026-07-08)

Cuatro cambios sobre la pestaña MASW / Waterfall. Todo verificado headless con
`QT_QPA_PLATFORM=offscreen` (ver scratchpad de la sesión); la GUI real no se pudo
correr desde acá.

**1. Editar la curva de dispersión desde "2. Inversion".** La curva experimental
en `inv_plot` pasó a ser un item persistente y editable (`_inv_observed_item`).
Botones "Mover" / "Borrar" (`_inv_edit_mode`). En "Mover" se arrastra un punto
**libremente en frecuencia y velocidad** (override de `mouseDragEvent` de la
viewbox, `_inv_vb_mouse_drag_event`): al soltar se re-clava el pick en su **nueva
frecuencia** (sale la clave vieja, entra la nueva). En "Borrar", click sobre un
punto lo saca (`_on_inv_mouse_clicked`). La cercanía se mide normalizada por el
rango visible de cada eje (`_inv_nearest_pick`). Editar sincroniza el scatter de la
pestaña 1 (`_refresh_pick_scatter`). `_run_inversion` ya no hace `inv_plot.clear()`:
mantiene el item medido y solo re-crea los modelos teóricos (`_inv_model_items`).

**2. M0 rectángulo → N regiones-polígono, multi-modo.** Reemplaza el `RectROI`
único de la Fase 8. Ahora se definen **N regiones (una por modo)**: cada región es
un polígono que se dibuja click a click. Botones "Iniciar región modo" /
"Cerrar región (nuevo modo)" / "Quitar regiones". Al cerrar (≥3 vértices) la región
se guarda como el modo siguiente (M0 fundamental, M1, ...). `_regions[m]` = polígono
del modo m; `picks_by_mode[m]` = curva del modo m; `self.picks` es un **alias**
(misma identidad de dict) al modo activo. Selector "Modo activo" (`active_mode_combo`)
elige qué modo se edita e invierte. `_auto_pick` con regiones arma **una curva por
región** (cresta restringida al polígono via ray-casting `_point_in_polygon` +
anti-aliasing); sin regiones, un solo modo sobre toda la imagen (comportamiento
clásico). Colores por modo (`_MODE_COLORS`). Verificado: 2 regiones → 2 curvas, cada
una dentro de su banda de velocidad.

**3. Persistencia y auto-restauración (waterfall + MASW).** Antes no se guardaba
nada de estas pestañas. Ahora al cerrar se persiste y al reabrir el mismo dataset se
restaura: `field_review_masw_state.json` (liviano: params de imagen/pick/inversión,
`picks_by_mode`, `regions`, escalares del resultado, ajustes de vista del waterfall)
+ `field_review_masw_state.npz` (pesado: datos crudos del waterfall y de MASW,
arrays del resultado de inversión). Helpers en `field_review_data.py`
(`default_masw_state_path`/`_arrays_path`, `load/save_masw_state`, `load/save_masw_arrays`).
Cada panel expone `get_state()`/`get_arrays()`/`restore_state()`; la ventana los
orquesta en `_restore_masw_state` (en `__init__`) y `_save_masw_state` (en
`closeEvent`). Al restaurar MASW se re-calcula la imagen (determinístico) y se
redibuja el resultado de inversión sin re-correrlo (`_redraw_inversion_from_result`).
Verificado: round-trip de picks_by_mode, regiones, resultado y estado del waterfall.

**4. Inversión conjunta multi-modo (evodcinv + disba).** Módulo nuevo
`masw_multimodal.py`. Se evaluaron ADsurf, Geopsy/Dinver+SWprepost y evodcinv; se
eligió **evodcinv 2.2 + disba 0.7** por ser Python puro, pip-installable y con modos
superiores reales (disba = forward CPS acelerado con Numba; evodcinv = inversión
CPSO multi-curva). Botón "Inversión conjunta multimodo": toma `picks_by_mode` (modos
con ≥3 picks), ajusta un único perfil de capas a todas las curvas a la vez, y
dibuja por modo la curva medida (color) vs teórica (punteada) + el perfil Vs.
Opcional y con guardas: `masw_multimodal.available()`; si falta, avisa
`pip install disba evodcinv` y sigue andando el flujo Monte Carlo de un modo.
**Compat NumPy 2:** evodcinv 2.2.x usa `np.Inf` (removido en NumPy 2.0);
`_patch_numpy()` re-agrega los alias antes de importarlo. `requirements.txt` lista
disba/evodcinv como opcionales. Verificado: inversión de 2 modos sintéticos devuelve
perfil + misfit + curvas teóricas sin crash.

**Pendientes anotados:**
- El resultado de la inversión **multimodo** (`_mm_result`) NO se persiste entre
  sesiones (sí los picks/regiones, así que se re-corre). Solo el resultado del flujo
  Monte Carlo de un modo (`_inv_result`) se guarda/restaura.
- La línea que une los vértices de las regiones es de un solo color; la distinción
  por modo va en los vértices y la etiqueta M0/M1/... (limitación de un `PlotDataItem`).
- La barra de progreso de evodcinv imprime a stdout (no hay callback de progreso en
  su API); en la GUI corre con cursor de espera y bloquea hasta terminar.

**Adenda — longitud de onda máxima λ_max = L (2026-07-08).** Además del piso
anti-aliasing `c ≥ 2·dx·f` (λ ≥ 2·dx), el picking ahora tiene techo por el largo
del arreglo: `λ_max = L` (L = span de distancias, `_estimate_array_length` = max−min;
en Canchita ≈ 40 m). Como λ = c/f, la condición λ ≤ L es `c ≤ L·f`. `_lambda_max_velocity(f)`
= `L·f` (o +inf si L desconocido); `_valid_velocity_mask` exige la banda completa
`2·dx·f ≤ c ≤ L·f`; "Añadir" rechaza también por arriba; `_calculate` enmascara a
NaN la zona `c > L·f` y dibuja la línea `c = L·f` en amarillo punteado
(`_draw_lambda_max_boundary`). L se persiste (`array_length_m`) y se recalcula al
restaurar. Verificado: con distancias span 40 m, `λ_max_vel(10 Hz)=400 m/s`, la máscara
excluye `c>L·f` y `c<2·dx·f`, y todo pick del auto-pick cae en la banda.
