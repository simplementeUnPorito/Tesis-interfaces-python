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

---

## Fase 10 — Regiones por modo, selector de motores, exportadores y filtro-K bidireccional (2026-07-08)

Rediseño del flujo multi-modo y multi-backend. Verificado headless (offscreen):
`test_multimode2` 13/13, `test_masw_features` 21/21, `test_lambda` 8/8,
`test_panel_backends` 6/6, `test_backends` 10/11 (1 assert viejo de tamaño).

**Regiones por modo (antes: 1 región = 1 modo por orden de dibujo).** Ahora
`_regions_by_mode: dict[int, list[polígono]]`: un modo puede tener VARIAS regiones,
y la que se dibuja se agrega al MODO ACTIVO. Botón **'+ Agregar modo'** (`_add_mode`,
M0→M1→…, deja el nuevo activo). 'Iniciar región'/'Cerrar región' operan sobre el
modo activo; 'Quitar regiones del modo' limpia solo ese modo. **Auto-pick** arma una
curva por cada modo con regiones, usando la UNIÓN de sus polígonos
(`_velocity_mask_polys`), y registra cada curva en su modo. **'Limpiar picks'** ahora
vacía `picks_by_mode[activo]` directamente (antes borraba vía alias y confundía). Se
agregó **leyenda de colores** por modo (`mode_legend_label`, `_refresh_mode_legend`).

**Inversión usa TODAS las curvas.** `_run_selected_inversion` despacha según el motor
elegido: evodcinv y disba+MC invierten en conjunto todas las curvas de modo;
maswavespy (1 modo) corre el Monte Carlo en vivo sobre el fundamental/activo.

**Selector de motores (`masw_backends.py`).** Combo 'Motor de inversión' con:
- `evodcinv`  — evodcinv+disba multimodo (`masw_multimodal`).
- `disba_mc`  — disba forward multimodo + Monte Carlo propio (búsqueda aleatoria).
- `maswavespy`— port numpy (`masw_inversion.monte_carlo_inversion`), 1 modo. El
  paquete real de third-party/maswavespy necesita Cython (`cy_theoretical_dc`, sin
  compilador acá); su `combination.CombineDCs` (numpy puro) sí se importa (path
  inyectado por `_ensure_maswavespy_on_path`).
- `adsurf`, `geopsy` — externos: `export_curves` escribe las curvas por modo en su
  formato (ADsurf: freq/vel txt; Dinver: freq/slowness target) + README, y
  `launch_tool` intenta abrir el ejecutable si está en el PATH. Botón 'Exportar
  curvas (CSV)' para el formato genérico. Todos los resultados in-proc devuelven el
  dict normalizado {beta, h, misfit, modes, theoretical, engine}.

**Filtro K con sentido elegible.** `fk_directional_filter` ya tenía `keep_forward`;
el combo 'Filtro K' (Off/Directo/Inverso) lo expone porque cuál es el sentido
"correcto" depende del tendido (el que estaba fijo mostraba a veces el rebote).

**Persistencia:** se guardan `regions_by_mode` (con compat al formato viejo `regions`),
el motor elegido (`backend`), y `kfilter_mode` (compat con el bool viejo).

**Demo:** `masw_demo.py` levanta solo el panel MASW con un gather sintético de dos
crestas (o un `.npz` con common_time/distances/matrix) para probar regiones + todos
los motores sin el dataset de campo. `python masw_demo.py`.

**Pendientes anotados:**
- ADsurf no se integró en-proceso (es repo GitHub con sus deps); se exporta/lanza.
- El resultado multimodo (`_mm_result`) sigue sin persistirse entre sesiones (sí los
  picks/regiones y el resultado del port maswavespy vía `_inv_result`).
- 'Auto inversion' (un clic desde Waterfall) usa el flujo maswavespy de un modo, no
  el motor seleccionado.

---

## Fase 11 — Smoke testing exhaustivo, robustez de 'Auto inversion', .target real y relevamiento (2026-07-08)

**Smoke testing (offscreen, todo verde):**
- App REAL completa con el dataset de Canchita (365 shots, 77 dup): construye
  ventana, **auto-restaura el masw_state viejo (compat con formato pre-Fase 10)**,
  cambia de tab, modo oscuro, filtro-K Off/Directo/Inverso, Ver MASW, calcular,
  auto-pick, cerrar (guarda formato nuevo con `regions_by_mode`). 12/12.
- MASW sobre datos REALES (21 canales, L≈40 m): imagen, 2 regiones→2 curvas, los 3
  motores in-proc (maswavespy/disba_mc/evodcinv), guardar CSV, tema, restore vacío,
  y casos borde (inversión con <3 picks avisa, export sin curvas avisa). 10/10.
- Handlers de mouse por eventos mock (con el gate de `sceneBoundingRect` neutralizado,
  que offscreen queda degenerado): dibujo de polígono, Añadir/Borrar, arrastrar (mueve
  velocidad, freq fija), y edición en la pestaña de inversión (borrar + arrastrar libre
  re-clava freq). OK. (Un par de "fallos" iniciales eran artefactos del test: puntos
  exactamente en el techo del rango c, que el ray-casting excluye por estar en el borde.)

**'Auto inversion' más robusta.** `auto_extract_dispersion_curve` (extractor coherente,
no lo escribí yo) es estricto y en capturas ruidosas reales tira "no hay cresta
coherente" y abortaba. Ahora `run_auto` **cae al `_auto_pick` simple** (cresta por
frecuencia en la banda 2·dx ≤ λ ≤ L) si el coherente falla, así el botón siempre
produce algo o da un mensaje claro. Verificado: en datos reales pasó de 0 picks a 90 +
inversión OK.

**Export .target real de Dinver (swprepost).** `export_curves('geopsy', …)` ahora usa
`swprepost.Target.to_target` para escribir el `.target` real (XML gzip) por modo, listo
para 'Load target' en Dinver; si swprepost no está, cae al texto simple. `pip install
swprepost` (en requirements).

**Relevamiento de qué más ensamblar:** ver `MASW_INTEGRATIONS.md`. Ranking:
1. (hecho) swprepost → .target de Dinver.
2. **swprocess** (Vantassel) → imagen MASW en Python con **std de la curva** por modo,
   para validar la nuestra y alimentar `velstd` a evodcinv/BayHunter.
3. **BayHunter** (GFZ) → inversión bayesiana McMC con **bandas de incertidumbre** y
   n° de capas como incógnita (lo más "tesis"); forward SURF96 vía pysurf96.
4. **obspy** → export SEG-Y/SU del gather crudo para que Geopsy haga su propio picking.
El router `masw_backends` está hecho para sumar estos como backends/exportadores sin
tocar la UI.

---

## Fase 12 — Auditoría de Fases 9-11 (2026-07-08, revisor distinto)

Code review completo del diff (commiteado en b882918c "Casi casi" = hasta Fase 9;
working tree = Fases 10-11) + pyflakes + tests de borde dirigidos. Resultado:
4 bugs reales corregidos, resto verificado sano.

**Corregido:**
1. **`disba_mc` y `evodcinv` crasheaban con Capas=1** (el spin permite 1) con un
   ValueError críptico de numpy en `column_stack`. Ahora ambos validan y andan.
2. **Semántica de "Capas" inconsistente entre motores**: para el port maswavespy
   n_layers=3 ⇒ 3 capas + semiespacio (beta de 4), pero para evodcinv/disba_mc
   significaba 3 en TOTAL (beta de 3) — el mismo spin producía modelos distintos
   según el motor. Unificado a la convención del port (capas SOBRE el semiespacio):
   `masw_multimodal` agrega n_layers+1 Layer a evodcinv, `disba_monte_carlo` usa
   n_layers espesores + n_layers+1 velocidades. Verificado: los 3 motores devuelven
   (beta=4, h=3) con Capas=3.
3. **Riesgo de pérdida de picks guardados**: `_restore_masw_state` envolvía los DOS
   restores (waterfall y MASW) en un solo try — si el del waterfall lanzaba, el de
   MASW no corría, los picks quedaban vacíos en memoria y el `closeEvent` los
   pisaba en disco. Ahora cada restore tiene su propio try.
4. **`run_auto` (camino coherente)** no refrescaba el combo de modos → mostraba
   "0 picks" tras el auto-pick. Agregado `_refresh_mode_combo()`.

**Verificado sin problemas (con tests dirigidos, todos verdes):**
- Datos del usuario intactos tras los smoke con el dataset real (annotations 365=365,
  ni entradas nuevas ni perdidas). El masw_state migró al formato nuevo, como debía.
- Migraciones de formato: `regions` viejo → `regions_by_mode`, `kfilter` bool →
  combo (True→Directo, False→Off).
- Leyendas de pyqtgraph NO acumulan entradas en corridas repetidas (multimodo ni MC vivo).
- Guardado con paneles vacíos: npz se omite/borra intencionalmente y el restore de
  estado vacío no crashea. El backend elegido persiste.
- pyflakes: solo imports de sondeo intencionales (`import disba  # probe`).
- Curvas de 3 puntos (mínimo) invierten sin crash.

**Anotado, sin acción:**
- `_valid_velocity_mask` quedó sin llamadores en producción (la usan los tests y
  documenta la banda 2·dx·f ≤ c ≤ L·f); se deja.
- Si el restore del waterfall falla pero el de MASW anda, el npz re-guardado pierde
  los arrays wf_* (recomputables con 'Ver waterfall' en un click). Aceptado.
- El commit b882918c fue del usuario a mitad de sesión; el working tree tiene
  Fases 10-11 + estos fixes, sin commitear.

Suites (todas verdes tras los fixes): fullapp 12/12 · masw_real 10/10 · clicks 4/4 ·
panel_backends 6/6 · multimode2 13/13 · features 21/21 · lambda 8/8 · auto_inv 5/5 ·
persist 5/5 · edge 4/4 · consistency 4/4.

## Fase 13 — Polaridad del geofono en el Waterfall: flip manual + auto-enfase en 2 etapas (2026-07-08)

**Problema.** El geofono funciona conectado en cualquier sentido, pero conectado
al revés la señal queda invertida — según el día de campo pudo quedar en
contrafase. El usuario ya dejó en fase las capturas validadas DENTRO de cada
punto (ej. todas las de 12 m coinciden entre sí), pero un punto entero puede
estar en contrafase respecto a sus vecinos (12 m vs 10/14 m), lo que destruye
el waterfall y el MASW. Además las capturas sin validar no tienen polaridad
controlada.

**Semántica clave.** Todo se corrige a nivel DATO vía `geo_flip` por captura
(persistido en `field_review_annotations.json`) — NO es un ajuste de vista como
el recorte/trazas ocultas del waterfall. Invertir una "traza" del waterfall =
togglear `geo_flip` en TODAS las capturas de esa distancia: como promedio,
filtro y resampleo son lineales, negar todos los miembros equivale exactamente
a negar el promedio, así que la fila del waterfall se niega en memoria
(`WaterfallPanel.flip_row`) sin recomputar, y el próximo "Refrescar promedios"
reproduce lo mismo (la `annotations_signature` incluye `geo_flip`, así que el
cache de promedios se invalida solo).

**UI nueva (pestaña Waterfall):**
- **"Invertir traza"** (debajo de la lista de trazas): invierte/des-invierte la
  distancia seleccionada. `FieldReviewWindow._flip_distance_group` →
  `flip_distance_group()` (field_review_data) + autosave + `flip_row` + refresh
  del plot de Capturas. Es el override manual si el auto no convence.
- **"Auto polaridad"** (fila superior; no confundir con "Auto inversion" que es
  la inversión MASW): corre `auto_align_polarity()` y muestra resumen. Si la
  etapa B cambió puntos validados, recalcula promedios y re-muestra el waterfall.

**`auto_align_polarity()` (field_review_data.py), dos etapas:**
1. **Intra-punto**: SOLO capturas sin validar (accepted y no reviewed). Se
   preparan con el mismo pipeline que `compute_average_groups`
   (`_prepare_shot_for_grouping` + resampleo a fs común + alineado al trigger)
   y se comparan por producto punto (sin lag, ya están alineadas) contra el
   consenso (nanmean) de las VALIDADAS de su punto; si da negativo se les
   togglea `geo_flip` con `source="auto_polaridad"`. Las validadas no se tocan
   nunca: el flip es una propuesta que el usuario acepta al revisarlas en
   Capturas. Punto sin validadas → consenso = mayoría actual de las no
   validadas (cambios mínimos).
2. **Inter-punto**: promedios por distancia (solo validadas, igual que el
   waterfall), encadenados desde la distancia MENOR (ancla). Correlación
   cruzada completa (`scipy.signal.correlate`, la búsqueda de lag absorbe el
   moveout entre puntos; demean + NaN→0 ANTES de resamplear a fs común porque
   `resample_poly` propaga NaN); pico de |corr| negativo → se invierte el punto
   COMPLETO vía `flip_distance_group` (validadas Y sin validar, para no romper
   la fase interna).

Idempotente (segunda corrida no cambia nada). No escribe a disco; el caller
autosavea. Test sintético (scratchpad `test_auto_polarity.py`, verde): 3 puntos,
uno con el punto entero en contrafase + 1 candidata cruzada, otro con 1
candidata invertida — detecta exactamente esos flips, y la candidata del punto
contrafase termina con `geo_flip=False` (etapa A la togglea, etapa B la
des-togglea junto con el punto: coherente con el punto ya corregido). Smoke
offscreen del panel (`test_waterfall_panel.py`, verde): botones, callbacks,
`flip_row` niega solo la fila pedida.

**Ojo**: el comentario viejo de `PickAnnotation.geo_flip` decía "el circuito no
tiene polaridad"; corregido — sí importa el sentido de conexión, la señal queda
invertida y eso destruye el promediado.

## Fase 14 — Enfase POR CARPETA + promedio de carpeta en Capturas (2026-07-08)

**Workflow nuevo (pedido del usuario):** en Capturas se pone BIEN el trigger de
cada señal (ese es ahora el ajuste fino, con el promedio de su carpeta como
referencia); en Enfase se calibra el desfase ENTRE DIAS trabajando por carpeta
(no señal por señal, que era lento: 32 señales por label); en Waterfall se
revisa si falta algo de fase.

**Enfase (`AlignmentPanel`) reescrito a por-carpeta:**
- Navega carpetas del label ordenadas por pico a pico del PROMEDIO de la
  carpeta, descendente. La primera define el 0 (se confirma con OK en 0.000).
- El gráfico muestra el promedio de cada carpeta ya confirmada (un color por
  carpeta, leyenda con el nombre; `legend.clear()` en cada redraw para no
  acumular entradas) + el promedio de la carpeta actual resaltado, que se
  mueve entero con el offset en vivo.
- Promedio por carpeta: trigger-alineado SIN offsets (la carpeta se corre
  rígida después con `grid - offset`), interpolación a grilla común para fs
  mezcladas, precalculado por label en `self._folder_traces` → mover el spin
  no recarga nada (rápido).
- "OK alineado" guarda `alignment_offsets[label][carpeta]` (estructura que ya
  era el default por señal en `get_alignment_offset`) **y limpia los offsets
  por señal viejos de esa carpeta** (tenían prioridad y pelearían con el
  ajuste de carpeta). El contador de legados se muestra en el texto de la
  carpeta. "Reset esta carpeta" saca solo esa; "Reset todo el label" limpia
  offsets de carpeta Y por señal del label (igual que antes).
- Entran las señales `accepted` (no hace falta `reviewed`, igual que el panel
  viejo).

**Capturas: checkbox "Promedio carpeta"** (junto a los overlays, default ON):
superpone en el geo_plot el promedio trigger-alineado de las señales
accepted+reviewed de la MISMA carpeta, excluyendo la actual (verde `#2ca02c`,
`_folder_average_for_shot`, misma mecánica liviana que
`_ok_average_for_distance`: cache de señales, sin resamplear fs — dentro de
una carpeta la fs es una). Sirve para calzar el trigger de cada señal contra
el consenso de su día.

Tests offscreen (scratchpad, verdes): `test_alignment_panel.py` (orden por p2p
del promedio, primera define el 0, OK guarda offset de carpeta y limpia
per-shot legado, resets) y `test_folder_avg_capturas.py` (ventana completa:
n correcto excluyendo actual y sin validar, overlay aparece/desaparece con el
checkbox). pyflakes: sin hallazgos nuevos (QKeySequence/get_alignment_offset/
masw_multimodal ya estaban sin uso en HEAD).

## Fase 15 — "Rechazar esta carpeta" en Enfase: excluir sin tocar Capturas (2026-07-08)

**Pedido del usuario:** que una carpeta sea "válida" en Capturas (trigger y
forma coherentes con el resto del grupo) NO implica que tenga que entrar al
waterfall — puede querer excluirla igual (ej. no confía en cómo quedó
enfasada contra las demás, o algo la hace sospechosa) sin marcar sus señales
como inválidas ni tocar el trigger.

**Dato nuevo, independiente de `PickAnnotation`:** `disabled_folders: dict[str,
list[str]]` (`{label: [carpeta, ...]}`), persistido en
`alignment_disabled_folders.json` (`load_disabled_folders`/
`save_disabled_folders`/`default_disabled_folders_path`, mismo patrón que
`alignment_offsets`). Chequeo: `is_folder_disabled(disabled, distance_m,
folder_name)`. Firma para invalidar cache: `disabled_folders_signature`.

**Filtro aplicado en:** `compute_average_groups`, `export_processed` (la
muestra individual se exporta igual — solo no entra al `grouped` del
promedio) y `auto_align_polarity` (no participa del consenso intra-punto ni
de la cadena inter-punto). Verificado con datos sintéticos: `n` del grupo cae
de 4 a 2 al desactivar una carpeta, y el manifest del export confirma que el
promedio real usó solo 2 (la muestra individual de la carpeta desactivada
sigue en `muestras/`).

**UI (`AlignmentPanel`):** botón toggle "Rechazar esta carpeta" debajo de OK
alineado. Es ortogonal al offset: podés tener una carpeta acumulada (offset
guardado) Y rechazada a la vez — sigue apareciendo como referencia en el
gráfico (línea punteada, "carpeta (rechazada)" en la leyenda) para poder
alinearla igual por si algún día se reactiva. "Reset esta carpeta" y "Reset
todo el label" también limpian el rechazo. El estado se refleja con
`blockSignals` en `_show_current` (mismo patrón que offset_spin) para no
disparar `_toggle_reject` en bucle al navegar.

Wiring: `FieldReviewWindow.disabled_folders` cargado en `__init__`, pasado a
`AverageReviewPanel`, `AlignmentPanel` y `auto_align_polarity`; guardado junto
con los demás offsets en `_alignment_offsets_changed`. `review_field_data.py
--export-only` también lo carga y pasa a `export_processed`.

Test nuevo (`test_disabled_folders_data.py`, scratchpad): confirma exclusión
real en `compute_average_groups`/`export_processed` (no solo de UI), checks
de `is_folder_disabled` cross-label, round-trip de persistencia y estabilidad
de la signature. Test de UI extendido en `test_alignment_panel.py`: toggle,
persistencia visual al navegar, limpieza en ambos resets. pyflakes limpio
(saqué `is_folder_disabled` de los imports de field_review_app.py: solo se
usa dentro de field_review_data.py).

## 2026-07-09 — Reorganización: `procesados/` en vez de `Crudos/`, motores MASW reales, ADsurf/Geopsy vendorizados

**Motivación del usuario:** todo lo que la app genera (anotaciones, sesión,
estado MASW, filtros, offsets de enfase, export `_procesado`) vivía adentro
de `Crudos/`, mezclado con los datos crudos del hardware. Además el motor
`adsurf` de la pestaña MASW era un placeholder de solo-exportar (no invertía
nada in-proc) y `geopsy` fallaba con "no encontré el ejecutable" porque nunca
había un `dinver.exe` real disponible.

**`Crudos/` → `procesados/` (raíz del repo):** `field_review_data.py` ganó
`_procesados_dir_for(raw_root)` y `_PROCESADOS_ROOT` (`<repo>/procesados/`).
Los 9 `default_*_path(raw_root)` (anotaciones, filtro, offsets de enfase,
offsets por shot, carpetas deshabilitadas, grupos de dispersión, sesión,
estado MASW json+npz) y `default_output_dir(raw_root)` ahora resuelven ahí
en vez de adentro/al lado de `raw_root`. `Crudos/` sigue siendo *solo* la
entrada cruda (`DEFAULT_RAW_ROOT` sin cambios). Se migró todo lo ya generado
(`Crudos/Canchita/field_review_annotations.json` y demás jsons sueltos,
`Crudos/Canchita_procesado/`, `Crudos/Canchita_procesado.zip`,
`Crudos/Canchita_grupo1_procesado/`) a `procesados/`, incluyendo los ~374
archivos que estaban trackeados en git pese a que `Crudos/` ya figuraba en
`.gitignore` (se hizo `git mv` + `git rm --cached -r procesados/` para que
`procesados/` quede realmente ignorado, no repetir el mismo problema).
`Crudos/Canchita_grupo1_paquete_tutor.zip` y `Crudos/Crudos.zip` se dejaron
donde estaban: son archivos armados a mano (paquete para el tutor / backup),
no genera nada el código para ellos.

**Motor `adsurf` pasó de "exportar/lanzar" a in-proc de verdad:**
vendorizado como submódulo git en `third-party/ADsurf`
(github.com/liufeng2317/ADsurf) + wrapper nuevo `masw_adsurf.py`. Corre la
inversión real por diferenciación automática (PyTorch/Adam) contra TODOS los
modos. Encontrados y esquivados dos bugs de la librería original: compara
`self.device=="cpu"` contra un string (rompe si le pasás `torch.device`, hay
que pasarle el string `"cpu"`), y `inversion_method="vs-and-thick"` dispara
early-stopping casi al toque (se usa `"vs"`, converge normal). Requiere
`pip install torch tqdm pandas seaborn` (CPU alcanza, no hace falta CUDA).

**Motor `geopsy` (Dinver) ahora lanza de verdad:** geopsy.org publica un zip
portable sin instalador (`geopsypack-win64-*.zip`, GPL3). Se vendoriza en
`third-party/geopsy/` (gitignored, ~80MB — no es código fuente propio, es un
binario de terceros, por eso NO es submódulo git sino una descarga). Nueva
`masw_backends.ensure_geopsy()` lo descarga/extrae solo si falta (p.ej. clon
nuevo del repo) y `_which()` lo encuentra ahí aunque no esté en el PATH del
sistema. Verificado con `dinver.exe --version` (imprime versiones reales de
Qt6/DinverCore, sin DLLs faltantes) y lanzando el proceso real vía
`launch_tool("geopsy", ...)`.

**Testing "de verdad" (no funciones sueltas reimplementadas):** se instanció
`MaswPanel` real y se le restauró el estado MASW real guardado
(`procesados/Canchita/field_review_masw_state.json` + `.npz`, 112 picks
reales del dataset Canchita), y se corrieron los 5 motores del combo
`backend_combo` llamando exactamente a `_run_selected_inversion()` /
`_export_to_tool()` — los mismos métodos que disparan los botones de la UI,
no reimplementaciones — con resultados finitos y misfit razonable en los 5
(evodcinv 0.0103, disba_mc 0.0116, maswavespy 7.06 [%rel, otra escala],
ADsurf 0.0098 km/s RMSE, geopsy exportó `.target` real). También se instanció
`FieldReviewWindow` completa (constructor real, con dataset real de 700+
shots) y se corrió `review_field_data.py --export-only` end-to-end (598
muestras, 21 promedios, manifest + waterfall PNG/PDF), confirmando que todo
cae en `procesados/` y `Crudos/` queda intacto.

**Pendiente / no automatizable:** confirmación visual con captura de
pantalla de la GUI real no se pudo completar porque la sesión de Windows
estaba bloqueada en el momento; toda la verificación de esta sesión fue
programática pero contra las clases y métodos reales, no contra scripts de
prueba reimplementados.
