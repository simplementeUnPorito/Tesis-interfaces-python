# Trazabilidad del plan web sísmico/MASW

Última revisión: 2026-07-27  
Base del diff: `10d9fb8f77ec`  
Gate actual: `58/58` en
`C:\Github\Tesis\tmp\implementation_smoke_fable_remediated_final_20260727`

Leyenda:

- **PROBADO**: existe evidencia ejecutada proporcional al requisito.
- **IMPLEMENTADO / FALTA EVIDENCIA**: el código está, pero la aceptación pedida
  exige una prueba más fuerte.
- **EXTERNO**: no puede demostrarse en este host sin una dependencia externa.
- **FUERA DE ALCANCE**: exclusión explícita del plan.

## 1. Estabilización y compatibilidad

| Requisito | Estado | Implementación | Evidencia |
|---|---|---|---|
| FastAPI + JavaScript nativo + Canvas | PROBADO | `server/api.py`, `server/static/js` | `refactor.stack_fastapi`, navegación por 8 tabs |
| Puerto 8000 estable y configurable | PROBADO | `server/app.py` (`--port`, `TESIS_PORT`) | arranque real y `--help` |
| Conflicto de puerto accionable y exit 2 | PROBADO | pre-bind en `server/app.py` | prueba manual documentada |
| `/api/meta`: Git, uptime, raíces e inicio | PROBADO | `server/api.py` | `base.meta`; cabecera actual/antigua en navegador |
| Auditoría sobre datos reales sin POST | PROBADO | `--read-only`, `TESIS_READ_ONLY` | `base.read_only_guard` |
| `TESIS_DATA_ROOT` mueve las tres raíces | PROBADO | defaults de `server/app.py` | `distribucion.data_root_aisla_todo`, ingesta incluida |
| Preservar rutas existentes | PROBADO | routers FastAPI | `base.interfaces_publicas`, OpenAPI |
| Escritura atómica y lock por campaña | PROBADO | `server/state.py`, locks compartidos en `field_review_data.py` | `estado.revision_optimista`, test multiproceso de cuarentena |
| `base_revision` obsoleta → 409 | PROBADO | routers de estado | `estado.revision_optimista`, `estado.campanas_revision`, round-trip MASW |
| Smoke falla si falta una comprobación obligatoria | PROBADO | `REQUIRED_FULL_GATE_IDS` + `--require` | el gate completo valida 58 ids antes de arrancar |

## 2. Ingesta ZIP

| Requisito | Estado | Implementación | Evidencia |
|---|---|---|---|
| Conservar `POST /ingest`, CORS y respuesta rápida | PROBADO | `routers/ingest.py` | `base.ingest_responde_rapido`, CORS preflight/POST |
| Trabajo persistente/reiniciable | PROBADO | `Pipeline`, `jobs.json`, cola propia | `reinicio.trabajos_persistentes` |
| Recepción streaming y publicación atómica | PROBADO | incoming temporal → staging → `replace` | `ingesta.zip_seguro_idempotente`, ausencia de staging |
| Máximo de upload | PROBADO | `limits.MAX_UPLOAD_BYTES` | cuerpo >2 MiB bajo cota de sandbox → 413 |
| Máximo de archivos | PROBADO | `limits.MAX_ZIP_FILES` | 65 archivos bajo cota 64 → 400 |
| Máximo expandido | PROBADO | `limits.MAX_UNCOMPRESSED_BYTES` | payload >1 MiB bajo cota de sandbox → 400 |
| Relación de compresión | PROBADO | `MAX_COMPRESSION_RATIO` | bomba ZIP y rechazo NaN/infinito |
| Rechazar absolutas, drive, `..`, truncado y CRC | PROBADO | `_validate_archive` | traversal/truncado; CRC en worker |
| Rechazar estructura inesperada | PROBADO | exige `metadata.json` y schema v4, con prefijo opcional | README-only, método no soportado y rutas duplicadas → 400 |
| Layout ESP sin carpeta prefijo | PROBADO | `metadata.json` en raíz | ZIP v4 atraviesa `/ingest` en el gate |
| SHA-256 idempotente, incluso concurrente | PROBADO | reserva bajo lock en `submit_zip_file` | reintentos secuenciales y simultáneos |
| Retener siempre el ZIP original | PROBADO | `data/server/zips` | hash/ruta comprobados; cuarentena no toca ZIP |
| Transporte `.geoq` excluido | FUERA DE ALCANCE | no añadido | exclusión del plan |

## 3. Capturas y filtros

| Requisito | Estado | Implementación | Evidencia |
|---|---|---|---|
| Validación, etiquetas, trigger, polaridad, distancia, overlays y navegación | PROBADO | `captures.py`, `picks.py`, `capturas*.js` | checks `capturas.signal.*` + recorrido navegador |
| Campos DC y supresión de línea con defaults | PROBADO | dataclass compartida en `field_review_data.py` | `filtros.ajustes_persisten` |
| Cadena raw/FIR → DC → SOS cero fase → armónicos | PROBADO | `apply_filter_chain` compartida | `filtros.preview_fase_cero`, supresor sintético |
| Supresor LS no lineal, no `iirnotch` | PROBADO | búsqueda f0 + sen/cos + `lstsq` | señales 50/60 desplazadas y varios armónicos |
| SOS ida/vuelta estable | PROBADO | SciPy `sosfiltfilt` | fase cero sintética |
| Hilbert sólo visual | PROBADO | endpoint preview/JS | ausencia en estado y resultados científicos |
| Archivos viejos conservan comportamiento | PROBADO | defaults compatibles | round-trip de ajustes y Canchita |
| PyQt y web usan la misma cadena | PROBADO | funciones en `geophone_scope.field_review_data` | ambos consumidores importan la misma implementación |
| Filtros lambda/f-k adicionales | OPCIONAL | f-k existe en Waterfall; lambda no | el plan los dejó opcionales |

## 4. Agrupamiento, Enfase, Promedios y Waterfall

| Requisito | Estado | Implementación | Evidencia |
|---|---|---|---|
| Selección múltiple y grupos por campaña | PROBADO | `grouping.py`, `agrupamiento.js` | `masw.geometria_multigrupo`, gate de grupos |
| Rechazo temporal, offsets, autoenfase y restauración | PROBADO | `alignment.py`, `enfase.js` | `enfase.clave_compartida`, `enfase.auto_y_restauracion`; navegador −19,96 ms |
| Promedios, arrivals y validación visual | PROBADO | `averages.py`, `promedios.js` | checks de Waterfall/promedios y E2E ZIP→Vs |
| Exportación reproducible de procesados | PROBADO | `exports.py`, cola científica | artefactos JSON/CSV |
| Wiggle persistido por campaña/grupo | PROBADO | estado Waterfall/MASW compartido | hashes visuales on/off y recarga |
| Traza + relleno sólo del lóbulo positivo | PROBADO | `areaVariable()` | inspección Canvas + hash visual |
| Raw amplitude, f-k, recorte, visibilidad, polaridad y autopolaridad | PROBADO | `waterfall.py/js` | `waterfall.vista_vs_persistente` |
| Ajustes visuales no mutan señales | PROBADO | vista separada | hash de anotaciones sin cambio |
| Enviar a MASW / automático | PROBADO | handoff localStorage con campaña/grupo | navegador: imagen 396×376; check de regresión |
| PNG, PDF, CSV y JSON | PROBADO | `run_waterfall_export` | descarga y contenido de los cuatro artefactos |

## 5. MASW

| Requisito | Estado | Implementación | Evidencia |
|---|---|---|---|
| JSON+NPZ PyQt autoritativo, sin migración | PROBADO | `masw.py` | Canchita 607 anotaciones, 2 grupos, 112 picks y resultado; SHA intactos |
| Preservar claves desconocidas y NPZ | PROBADO | merge conservador + revisión compuesta | `masw.roundtrip_json_npz` |
| Combinación ponderada de grupos | PROBADO | `_dispersion_group` + combinación | `masw.geometria_multigrupo` |
| Escalas log/lineal y normalización por frecuencia | PROBADO | estado + render Canvas + backend | gate de estado e inspección navegador |
| Edición/drag de picks | PROBADO | `masw.js` pointer events | arrastre real en Canvas sobre copia de Canchita: 112→112, exactamente 1 pick reemplazado y estado persistido |
| Múltiples modos y múltiples regiones | PROBADO | `picks_by_mode`, `regions_by_mode` | round-trip de modos 0/1 y varias regiones; export Geopsy de ambos modos |
| Límites aliasing/longitud de onda | PROBADO | respuesta dispersión | geometría multigrupo y render |
| Cálculo compartido, no algoritmos JS | PROBADO | endpoints llaman módulos Python | inspección de dependencias |
| Un proceso hijo, progreso, persistencia, cancelación real | PROBADO | `AnalysisJobs` | `masw.trabajos_artefactos_cancelacion`, reinicio |
| Revisión cambia durante inversión | PROBADO | artefactos antes del merge optimista | job `listo`, `persisted=false`, 3 artefactos y estado nuevo intacto |
| Backends evodcinv, MC/disba, MASWavesPy, ADsurf, Geopsy | PROBADO EN DESCUBRIMIENTO | registro de backends | UI muestra 5; dependencias ausentes se diagnostican |
| Ejecución reducida de cada backend disponible | PROBADO | maswavespy port + Geopsy export disponibles en este host | `masw.trabajos_artefactos_cancelacion` |
| Geopsy launch sólo Windows instalado | PROBADO ESTRUCTURAL | detección explícita | export reducido; no se lanzó durante auditoría |
| Original inmutable + copia editable | PROBADO | NPZ + `edited_profile` JSON | round-trip y UI Perfil |
| Exportar perfil, h, misfit, picks, curvas, config, seed y backend | PROBADO | artefactos de inversión | `masw.trabajos_artefactos_cancelacion`, E2E |
| Semiespacio homogéneo sintético | PROBADO | solver compartido | Vs=300 m/s → curva plana 276 m/s (~0,92 Vs) |
| Multimodo sintético | PROBADO | phase-shift y contratos por modo | crestas 180/340 m/s + export Geopsy modos 0/1 |
| swprocess, BayHunter y ObsPy excluidos | FUERA DE ALCANCE | no integrados | exclusión del plan |

## 6. “Borrado” reemplazado por cuarentena reversible

La orden posterior del usuario reemplaza cualquier borrado físico.

| Requisito vigente | Estado | Implementación | Evidencia |
|---|---|---|---|
| Filtros por fecha/sitio/distancia/carpeta/estados | PROBADO | `deletion.py`, `borrado.js` | catálogo y recorrido navegador |
| Selección múltiple, preview y confirmación | PROBADO | token ligado a claves/acción/revisiones | `borrado.preview_confirmacion` |
| Desactivar/restaurar sin eliminar | PROBADO | bandera global `__all__` | hashes raw/ZIP intactos |
| Rutas dentro de raíces autorizadas | PROBADO | resolución de claves, sin API física | auditoría Fable de cuarentena |
| Historial de operaciones | PROBADO | `deletion_history.json`, `deleted: []` | ciclo disable/restore |
| Compatibilidad `/delete` sin borrado | PROBADO | shims sólo aplican bandera | smoke heredado integrado |
| Sin retención automática | PROBADO | no existe scheduler/política | inspección de código |

## 7. Distribución

| Requisito | Estado | Implementación | Evidencia |
|---|---|---|---|
| Windows directo | PROBADO | `python -m server` | arranques reales, puerto/meta |
| Dependencias core/full fijadas | PROBADO | requirements separados | `pip check`, resolución dry-run |
| Docker no privilegiado + healthcheck + 3 volúmenes | PROBADO ESTRUCTURAL | Dockerfile/compose | `distribucion.contrato` |
| Un worker HTTP | PROBADO ESTRUCTURAL | comando uvicorn único | Dockerfile y arranque |
| Colas separadas ingesta/análisis | PROBADO | `Pipeline` / `AnalysisJobs` | ingesta concurrente durante inversión |
| Build/health/reinicio real en Docker | EXTERNO | artefactos listos | no hay Docker/Podman/nerdctl ni WSL distro en este host |

## Interfaces públicas

`base.interfaces_publicas` contrasta OpenAPI y prueba que navegador/ESP sólo
dependen de GET/POST/OPTIONS. Están cubiertos `/ingest`, `/health`, `/api/meta`,
jobs/cancel, export/artifacts, estado/dispersión/inversiones MASW y
catálogo/preview/desactivar/restaurar.

## Aceptación final

| Criterio | Estado | Evidencia |
|---|---|---|
| ZIP → validación → Waterfall → dispersión → picks → inversión → perfil Vs | PROBADO | `pipeline.e2e_zip_a_vs` |
| Abrir el mismo estado desde web y PyQt | PROBADO | Canchita + round-trip JSON/NPZ |
| Datos históricos intactos | PROBADO | SHA antes/después |
| Nada se elimina sin confirmación | SUPERADO POR REQUISITO POSTERIOR | no existe borrado físico; sólo bandera reversible |
| Pruebas visuales trigger/picks/regiones/jobs/wiggle/Borrado | PROBADO | wiggle, Borrado, autoenfase, handoff, backends, jobs/cancel y gestos Canvas recorridos | trigger arrastrado 0,0632→0,08549 s, pick arrastrado con cambio 1-a-1 y región de 4 vértices guardada, todo en sandbox |
| Docker: health, volúmenes, reinicio e ingesta durante inversión | EXTERNO PARCIAL | contrato e ingesta concurrente probados; runtime Docker ausente |

## Brechas activas

1. Ejecutar aceptación Docker en un host con engine.

La evidencia detallada de los gestos Canvas, incluidos hashes y coordenadas
persistidas, está en
`server/audits/20260727_browser_canvas_acceptance.md`. Es una aceptación E2E
registrada; todavía no forma parte del gate de consola.
