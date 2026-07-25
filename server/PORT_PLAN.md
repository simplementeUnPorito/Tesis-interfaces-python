# Plan de porteo: field_review_app (PyQt) → web del servidor

Documento de trabajo para ejecutar el porteo sin depender del contexto de la
sesión que lo escribió. Estado al 2026-07-24, rama `cambios-red`.

**Objetivo final**: la web del servidor reemplaza a `field_review_app.py` por
completo. Misma funcionalidad, distinta GUI; para el usuario la experiencia tiene
que ser equivalente. La app de escritorio se retira **cuando la web llegue a
paridad**, no antes.

---

## 0. Lo que ya está hecho (no rehacer)

| Pieza | Dónde | Estado |
|---|---|---|
| Ingesta del ZIP | `server/pipeline.py` | anda; probado con ZIP real de 830 kB |
| Cola de trabajos + persistencia | `server/pipeline.py` | anda; sobrevive reinicios |
| Picking automático en diferido | `server/pipeline.py` `_process()` | anda cuando hay hammer+geo |
| Catálogo que no descarta nada | `server/catalog.py` | anda; ve 210 capturas / 415 nodos |
| Borrado manual + barrido sin-martillo | `server/app.py` | anda, pero es un parche (ver §4) |
| Tablero de estado | `server/app.py` `INDEX_HTML` | anda; **hay que reemplazarlo** |
| Botón "Subir al server" en la SPA | `master/data/js/app.js` | anda de punta a punta |

**Direcciones**: campo `http://geo-obtain.local` · servidor `http://geo-data:8000`
(hostname de Tailscale ya renombrado; MagicDNS resuelve `geo-data`).

**Arranque**: `cd src/interfaces/python && python -m server --port 8000`
Por defecto `raw_root = <repo>/data/raw` y `data_root = <repo>/data/server`.

### Decisiones ya tomadas que hay que respetar

1. **Una sola fuente de datos.** `raw_root` es el mismo árbol que lee la app PyQt
   (`data/raw`). La web no tiene datos propios. Lo que ingesta el servidor lo ve
   la app de escritorio y al revés.
2. **Mismo formato de anotaciones.** Se escribe
   `procesados/field_review_annotations.json` vía `frd.save_annotations()`. No
   inventar un formato paralelo: las dos interfaces tienen que poder convivir
   durante toda la transición.
3. **Nada se borra solo.** Ni por estar incompleto, ni por antigüedad, ni por
   cuota. Sólo por pedido explícito de un humano. El ZIP original se conserva
   salvo pedido explícito (`zip=1`).
4. **Reusar, no reimplementar.** `field_review_data.py`, `signal_proc.py`,
   `masw_*.py` **no tienen Qt**: se importan tal cual. Si aparece la tentación de
   copiar una fórmula, es señal de que hay que exponer la función existente.
5. **LFS es automático.** El repo de datos tiene `/raw/** filter=lfs`, así que
   todo lo que el servidor escriba en `data/raw` queda bajo LFS sin trabajo extra.
   No agregar patrones ni tocar `.gitattributes`.

---

## 1. Refactor previo: FastAPI + estáticos (hacer PRIMERO)

Hoy el HTML está incrustado como string en `app.py`. Para gráficos interactivos,
tabs y picking eso no escala; hacerlo después implica reescribir dos veces.

FastAPI y uvicorn **ya están instalados** (0.140.0 / 0.51.0).

Estructura objetivo:

```
server/
  api.py          app FastAPI: routers, modelos, DI del Pipeline
  routers/
    ingest.py     POST /ingest  (mantener la ruta y el CORS tal como están)
    dataset.py    catálogo, capturas, señales
    picks.py      anotaciones (leer/escribir)
    masw.py       dispersión, inversión, perfil Vs
    admin.py      borrado, requeue, mantenimiento
  static/
    index.html    esqueleto con las tabs
    css/app.css
    js/
      main.js     router de tabs + estado global
      theme.js    toggle claro/oscuro (ver §2)
      plot.js     canvas/SVG reutilizable
      tabs/*.js   un módulo por tab
```

Requisitos que **no** se pueden perder en el refactor:

- `POST /ingest` con las cabeceras CORS actuales (`Access-Control-Allow-*`). La
  SPA se sirve desde el ESP32, o sea otro origen: sin CORS el navegador aborta en
  el preflight y en la SPA se ve como "servidor inalcanzable", lo que manda a
  buscar el problema a la red. Ya pasó.
- Responder el POST **antes** de preprocesar. El operador en el campo no espera.
- El worker en hilo aparte (`Pipeline`), no en el event loop: `auto_pick_shot`
  carga señales enteras y bloquearía el servidor.

---

## 2. Tabs y tema (paridad de navegación)

La app tiene exactamente estos tabs, y la web debe tener los mismos nombres:

`Capturas` · `Filtros` · `Agrupamiento` · `Enfase` · `Promedios / arrivals` ·
`Waterfall` · `MASW` (subtabs `1. Dispersion`, `2. Inversion`, `3. Perfil Vs`)

Más uno nuevo que no existe en la app: **`Borrado`** (§4).

**Tema claro/oscuro**: hoy sólo sigue `prefers-color-scheme`. Falta el toggle
manual, como en la SPA del maestro (`master/data/js/app.js` `initTheme()` —
copiar ese patrón: `data-theme` en `:root` + persistencia en `localStorage`, con
`prefers-color-scheme` como valor inicial).

---

## 3. Orden de porteo, tab por tab

Cada paso tiene que quedar usable por sí solo. No avanzar al siguiente sin que el
anterior funcione contra datos reales (hay 210 capturas en `data/raw`).

### 3.1 `Capturas` — el visor con pick editable (máxima prioridad)

Es el mínimo para que un humano valide de verdad, que es lo único que el
preprocesado no puede hacer.

- `GET /api/signal?shot_id=&kind=raw|filt&max_points=` → serie decimada para
  dibujar. **Decimar en el servidor**: una captura de 60 s a 2604 Hz son ~156 k
  muestras por canal y mandarlas crudas al navegador es inútil. Usar
  min/max por píxel para no perder picos.
- Dibujar hammer + geo con el trigger marcado; arrastrar el marcador → guardar.
- `POST /api/pick` → `arrival_s`, `accepted`, `reviewed=true`, `geo_flip`.
  Escribir con `frd.save_annotations()`.
- Respetar la convención de polaridad **fija**: geo no invertido, hammer
  invertido (`load_signal(apply_invert=True)` ya lo hace). No re-implementar.
- `geo_flip` por captura: el geófono conectado al revés graba en contrafase y eso
  destruye el promedio y el waterfall.

Reusar: `frd.load_signal`, `frd.auto_pick_shot`, `frd.detect_hammer_trigger`,
`frd.load_annotations`, `frd.save_annotations`.

### 3.2 `Filtros` y `Enfase`

- Parámetros de filtro persistidos con `frd.load_filter_settings` /
  `save_filter_settings` (ya existen, mismo archivo que la app).
- Filtrado con `signal_proc.py`: `dcRemove`, `filtFilt`, `harmonicNotch`,
  `hilbertEnvelope`. Son las mismas funciones que usa la app; llamarlas, no
  reescribirlas.
- Enfase por **carpeta** (no por captura): así lo hace la app y así limpia
  offsets por señal. Offsets con `frd.load_alignment_offsets` /
  `save_alignment_offsets`.

### 3.3 `Agrupamiento` y `Promedios / arrivals`

- Agrupar por distancia; promedio por carpeta.
- Promedios con `frd.load_average_arrivals` / `save_average_arrivals`.
- Conservar el botón **"Rechazar esta carpeta"**: excluye del promedio sin tocar
  la validez de las capturas individuales. Es una distinción que ya existe y que
  el usuario usa.

### 3.4 `Waterfall`

- Imagen servidor-side (PNG) o canvas con datos decimados; probar cuál responde
  mejor con 200+ capturas.
- Polaridad por punto (`geo_flip` vía grupo de distancia), botones "Invertir
  traza" y "Auto polaridad", auto-enfase en 2 etapas.
- Persistencia del estado del waterfall (la app guarda json+npz — mantener el
  mismo formato para no romper compatibilidad).

### 3.5 `MASW` (el grueso)

- `1. Dispersion`: imagen f-v con `masw_dispersion.py`; regiones-polígono
  editables → N curvas por modo (`masw_multimodal.py`). Persistir json+npz igual
  que la app.
- `2. Inversion`: backends de `masw_backends.py` — maswavespy (numpy, 1 modo),
  evodcinv+disba (multimodo), disba+MC propio, ADsurf (AD/PyTorch), Geopsy
  externo. **Corren como jobs**, no en el request: tardan minutos. Reusar el
  `Pipeline` (agregar tipos de trabajo) y mostrar progreso.
- `3. Perfil Vs`: resultado de la inversión, con curva editable.
- Ojo: `third-party/` (maswavespy, ADsurf, geopsy) se resuelve en runtime
  subiendo directorios desde `geophone_scope` (`masw_adsurf.py:32`,
  `masw_backends.py:470`). No mover esa carpeta.
- `masw_multimodal._patch_numpy()` re-agrega `np.Inf` antes de importar evodcinv
  2.2.x (removido en NumPy 2.0). Llamarlo, no duplicar el parche.

---

## 4. Ventana de borrado dedicada (reemplaza el parche actual)

Hoy hay dos botones sueltos. Lo pedido es **una ventana dedicada** con filtros y
clasificación, en vez de multiplicar botones.

- Tabla con todas las carpetas/capturas y columnas filtrables.
- **Banderas** para filtrar y clasificar (combinables):
  `sin martillo` · `sin geófono` · `sin picking` · `no validada` · `validada` ·
  `rechazada` · `duplicada` (`frd.DuplicateGroup` ya detecta duplicados por
  firma de señal) · `sin subir a LFS` · rango de fechas · por sitio/carpeta.
- **Subgrupos** por sitio y por distancia, colapsables, con conteos.
- Selección múltiple + "borrar seleccionadas", con confirmación que **lista** qué
  va a borrar.
- Opción explícita "borrar también el ZIP original" (por defecto **no**).
- El historial del trabajo queda marcado `borrado`, no se elimina: el registro de
  lo que llegó no se pierde.
- Mantener la regla conservadora del barrido: una carpeta sólo entra en
  "sin martillo" si **ninguna** de sus capturas tiene martillo.

---

## 5. Trampas ya encontradas (no volver a pisarlas)

1. **El proceso viejo no toma los cambios.** Pasó hoy: el servidor mostraba 1
   captura en vez de 210 porque seguía corriendo con el `raw_root` anterior.
   Reiniciar tras cambiar defaults.
2. **CORS**: sin las cabeceras, el POST desde la SPA falla con lo que parece un
   error de red. El error real sólo se ve en la consola del navegador.
3. **`uploadfs` del ESP borra LittleFS**: se va la cola `.geoq` y (antes) la
   config. La config del enlace ya está en NVS por esto.
4. **Sin martillo no hay picking**: `auto_pick_shot` detecta el trigger *en la
   señal del martillo*. Es geofísica, no un bug. Catalogar y marcar, nunca
   esconder.
5. **`discover_dataset` descarta capturas sin el par hammer+geo** porque su unidad
   es el disparo. Para catalogar usar `catalog.py`; `discover_dataset` sólo para
   la capa MASW.
6. **Windows/OneDrive**: `data/` está en un repo con LFS folder-backed; no
   escribir fuera de `raw_root` ni asumir que se puede renombrar a voluntad.

---

## 6. Criterio de "listo"

La web llega a paridad cuando, sobre los datos de `data/raw`:

- se puede validar un pick a mano y la app PyQt ve el cambio (y al revés);
- se puede promediar por carpeta y rechazar una carpeta;
- se puede generar la imagen de dispersión, marcar regiones y correr una
  inversión hasta el perfil Vs;
- la ventana de borrado permite filtrar por banderas y borrar por selección;
- el tema claro/oscuro se puede forzar a mano;
- todo eso desde `http://geo-data:8000` a través de la VPN, sin instalar nada.

Recién ahí se discute retirar `field_review_app.py`.
