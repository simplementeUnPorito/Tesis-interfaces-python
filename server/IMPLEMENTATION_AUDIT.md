# Pipeline web sísmico/MASW — registro de implementación y auditoría

Última actualización: 2026-07-26  
Repositorio: `src/interfaces/python`  
Rama observada: `cambios-red`  
Base Git al ejecutar la auditoría: `10d9fb8f77ec`

Este documento permite auditar el port web y retomarlo en otra sesión. Los
archivos bajo `data/raw` y `data/processed/<campaña>` siguen siendo
autoritativos; abrir una campaña nunca inicia una migración ni reescribe su
estado.

## Decisiones posteriores al plan

### 2026-07-26 — prohibición de borrado físico

El usuario reemplazó expresamente el requisito original de Borrado: ninguna
carpeta, captura ni ZIP debe eliminarse. El tab conserva su nombre por
continuidad, pero ahora funciona como cuarentena reversible.

La implementación usa la clave reservada `__all__` dentro del estado compartido
`alignment_disabled_folders.json`. PyQt y web conservan el formato
JSON existente; los cálculos compartidos interpretan esa clave como exclusión
global, independiente de distancia y grupo. Restaurar quita sólo esa bandera.
El raw, el ZIP, las anotaciones, offsets, grupos y resultados permanecen
intactos.

La auditoría externa y la resolución de sus cuatro hallazgos están en
[`audits/20260726_fable_quarantine.md`](audits/20260726_fable_quarantine.md).

### 2026-07-26 — incidente Waterfall → MASW

Un proceso anterior seguía activo en el puerto 8000 y, además, se corrigió una
carrera real al reanudar MASW desde otra campaña, la respuesta tardía de
`wiggle`, el selector de backends y el aislamiento explícito de
`data/processed` en sandboxes. Diagnóstico, hashes visuales y reproducción:
[`audits/20260726_ui_waterfall_masw_incident.md`](audits/20260726_ui_waterfall_masw_incident.md).

## Arquitectura resultante

- FastAPI sirve una SPA de JavaScript nativo y Canvas.
- `Pipeline` mantiene una cola persistente para ZIP de adquisición.
- `AnalysisJobs` mantiene otra cola persistente, con un único proceso científico
  hijo activo y cancelable.
- PyQt y web reutilizan `geophone_scope.field_review_data`; los estados
  científicos siguen en JSON+NPZ compartidos.
- Las mutaciones de estado usan escritura atómica, lock por archivo/campaña y
  control optimista `revision` / `base_revision`. Una revisión vieja responde
  `409`.
- El servidor expone `/api/meta` para identificar PID, commit Git, inicio,
  uptime y raíces activas. El puerto es configurable y un conflicto de bind
  termina con código 2 y un mensaje accionable.

## Ingesta

`POST /ingest` conserva su contrato rápido y CORS para el master ESP. La carga
se transmite a un temporal y luego se publica en la cola. Antes de responder se
valida estructura; el worker verifica CRC, extrae a staging y publica
atómicamente.

Controles aplicados:

- máximo de subida de 256 MiB;
- máximo 20.000 archivos;
- máximo expandido de 2 GiB;
- relación de compresión máxima 250×;
- contrato `geophone_scope_web_zip_v4`, compatible con capturas incompletas
  sin martillo o geófono pero no con ZIP arbitrarios;
- rechazo de rutas absolutas, letras de unidad, `..`, rutas con `:`, cifrado,
  compresiones no soportadas y ZIP truncado;
- SHA-256 e idempotencia incluso ante reintentos simultáneos;
- recuperación de estados pendientes/descomprimiendo/procesando tras reinicio;
- conservación predeterminada e indefinida del ZIP original.

No se añadió transporte `.geoq`.

## Capturas y filtros

Capturas conserva validación, aceptación/rechazo, trigger, distancia, etiqueta,
polaridad, overlays, navegación y operaciones largas. El filtro compartido
incorpora:

- `dc_enabled`;
- `line_suppress_enabled`;
- `line_f0_hz` (50 Hz);
- `line_harmonics` (3; máximo 12);
- `line_search_hz` (±2 Hz).

Cadena científica: selección raw/FIR existente → remoción DC → Butterworth SOS
de fase cero → supresor armónico adaptativo. Este último no es `iirnotch`:
busca la frecuencia de línea, arma senos/cosenos para sus armónicos, ajusta
globalmente por mínimos cuadrados y resta el modelo, igual que el concepto del
master ESP. La envolvente Hilbert existe sólo en la vista previa y no se
persiste ni participa en agrupamiento, promedios, Waterfall, autoenfase o MASW.
Los documentos antiguos sin campos nuevos conservan sus defaults previos.

## Agrupamiento, Enfase y Promedios

- Agrupamiento persiste cantidad de grupos y asignaciones por carpeta, con
  selección múltiple y revisión optimista.
- Enfase trabaja por carpeta y label, soporta offset, rechazo temporal,
  restauración por carpeta/label y limpieza de offsets por señal heredados.
- Se añadió autoenfase conservador: referencia de mayor pico a pico,
  correlación normalizada con signo, búsqueda limitada, umbral mínimo y rechazo
  de máximos ambiguos. No toca señales; sólo escribe los offsets compartidos.
- La clave de rechazo autoritativa sigue siendo `::grupoN`, compatible con
  PyQt; se lee y limpia la clave transitoria web `#gN`.
- Promedios/arrivals conserva el estado de validación visual y las llegadas
  aparentes con revisión optimista.

La decisión de signo del autoenfase y el dictamen externo están documentados en
[`audits/20260726_fable_autoenfase.md`](audits/20260726_fable_autoenfase.md).

## Waterfall y exportaciones

Waterfall conserva amplitud raw, filtro f-k directo/inverso, recorte, trazas
visibles, polaridad manual y autopolaridad compartida. `wiggle` se persiste por
campaña/grupo y dibuja/fotografía la traza con relleno de lóbulos positivos.
Los controles visuales no reescriben las señales.

“Enviar a MASW” y “MASW automático” transfieren campaña/grupo; el flujo
automático calcula dispersión, ejecuta auto-pick y lanza la inversión elegida.
La cola científica exporta CSV, PNG, PDF y JSON reproducibles y sirve cada
artefacto mediante `/api/artifacts/{id}`.

## MASW

- Carga directa del JSON+NPZ histórico; claves desconocidas y arrays NPZ se
  preservan.
- Revisión compuesta de JSON+NPZ: un cambio externo en cualquiera invalida una
  escritura web vieja.
- Combinación ponderada de grupos, normalización por frecuencia, escalas
  lineal/log, edición/arrastre de picks, múltiples modos y múltiples regiones.
- Límites por aliasing y longitud de onda.
- Procesos hijos persistentes, progreso, aislamiento, cancelación real y
  recuperación tras reinicio.
- Backends expuestos: evodcinv+disba, Monte Carlo/disba, MASWavesPy port,
  ADsurf y Geopsy export/launch.
- El mejor modelo original queda inmutable en NPZ; `edited_profile` es una copia
  editable por capas.
- Exporta perfil Vs, espesores, misfit, picks, curvas observadas/teóricas,
  backend, parámetros y semilla.

En el host auditado: MASWavesPy está disponible; Geopsy export+launch está
disponible; evodcinv/disba y ADsurf requieren instalar las dependencias
científicas. `requirements-full.txt` resuelve esas versiones.

Compatibilidad real comprobada con Canchita: 607 anotaciones, 2 grupos, 112
picks del modo 0 y resultado de inversión, sin cambiar los SHA de JSON/NPZ.

## Borrado

El tab nuevo filtra por fecha, sitio, distancia, carpeta y estados
`sin_martillo`, `sin_geofono`, `sin_picking`, `no_validada`, `validada`,
`rechazada`, `duplicada`, `sin_lfs` y `desactivada`.

- Desactivar y restaurar muestran una preview exacta y requieren un token
  ligado a claves, acción y revisiones de campaña.
- Una escritura concurrente vuelve obsoleta la preview y responde `409`.
- Web y PyQt coordinan `alignment_disabled_folders.json` con un lockfile entre
  procesos; un snapshot PyQt viejo preserva `__all__` al guardar Enfase.
- La desactivación excluye inmediatamente la carpeta de Capturas,
  Agrupamiento, Enfase, Promedios, Waterfall, MASW y exportaciones.
- El catálogo de Borrado siempre incluye las carpetas desactivadas para que
  puedan restaurarse.
- `deletion_history.json` se conserva como nombre histórico, pero registra
  eventos `disable`/`restore`, con `deleted: []`.
- `/api/deletion/delete`, `/api/delete` y `/api/delete-sin-hammer` permanecen
  por compatibilidad y ahora sólo aplican la bandera reversible.
- `/api/deletion/disable` y `/api/deletion/restore` son los nombres explícitos.
- `Pipeline.delete_folder()` está clausurado y devuelve un error seguro; no
  contiene `rmtree` ni borrado de ZIP.
- Una bandera cuya carpeta fue movida externamente sigue apareciendo como
  `desactivada + ausente` y se puede limpiar con Restaurar.

No existe una opción `with_zip`: los ZIP se conservan siempre.

## Distribución

- Windows: `python -m server`, puerto 8000 estable/configurable.
- Dependencias mínimas: `server/requirements-core.txt`.
- Dependencias científicas: `server/requirements-full.txt`.
- Docker: usuario `geophone`, healthcheck `/health`, un worker HTTP y volúmenes
  explícitos `/data/raw`, `/data/processed`, `/data/server`.

El 2026-07-26 `pip install --dry-run --ignore-installed -r
server/requirements-full.txt` resolvió todas las versiones fijadas. No había
Docker, Podman ni nerdctl en el host, por lo que la imagen y compose se
validaron estructuralmente, pero el build, healthcheck y reinicio dentro de un
contenedor siguen pendientes de ejecutar en una máquina con engine. WSL está
presente pero no tiene ninguna distribución instalada, por lo que tampoco
existe un engine alternativo allí.

## Interfaces públicas

Se conservaron `/ingest`, `/health` y las rutas previas. Se añadieron
`/api/meta`, consulta/cancelación de jobs, exportaciones/artefactos, estado,
dispersión e inversiones MASW, y preview/desactivación/restauración de
carpetas. OpenAPI sólo expone GET y POST para los clientes ESP/navegador.

## Evidencia de verificación

Gate completo:

```text
python server/smoke_test.py --json C:\Github\Tesis\tmp\implementation_smoke_fable_remediated_final_20260727\results.json
58/58 checks OK
```

Log: `C:\Github\Tesis\tmp\implementation_smoke_fable_remediated_final_20260727\logs\gate_20260727_000055.log`

El gate incluye:

- manifiesto explícito de 58 checks obligatorios para impedir verdes falsos;
- estructura v4, máximos de upload/archivos/expansión, traversal, ZIP
  truncado/bomba, CRC, SHA duplicado y publicación; incluye el layout real sin
  prefijo, rechazo de rutas duplicadas y límites NaN/infinito;
- DC, fase cero, línea desplazada 50/60 Hz, múltiples armónicos y exclusión de
  Hilbert del estado científico;
- conflictos 409, incluido `campaigns.json`;
- agrupamiento, geometría multigrupo, Enfase, autoenfase/restauración,
  promedios, arrivals, Waterfall y polaridad;
- round-trip JSON+NPZ y cambio NPZ externo;
- semiespacio homogéneo no dispersivo, gather de dos modos, combinación
  ponderada, export Geopsy multimodo, MASWavesPy reducido, artefactos y
  cancelación;
- preservación de artefactos si una inversión termina sobre una revisión MASW
  ya obsoleta, sin fusionarla en el estado nuevo;
- recuperación de ingesta y análisis desde estados interrumpidos;
- ingesta concurrente con inversión;
- E2E ZIP → validación → Waterfall → dispersión → picks → inversión → perfil Vs;
- cuarentena moderna y heredada: preview, 409 obsoleto, exclusión de Capturas,
  Agrupamiento y Promedios, restauración y hashes raw/ZIP intactos;
- contrato Docker y dependencias fijadas;
- modo de auditoría `--read-only` sobre datos reales y aislamiento completo al
  arrancar sólo con `TESIS_DATA_ROOT`.

Comprobaciones adicionales realizadas:

- `python -m compileall server`;
- `python -m pip check`;
- `git diff --check`;
- resolución `pip --dry-run` de requisitos completos;
- bind ocupado devuelve código 2;
- navegación manual por los ocho tabs;
- autoenfase real en navegador: retraso sintético de +20 ms → offset
  `-19,96 ms`;
- `wiggle` activado, cambio visual confirmado y persistencia comprobada;
- `wiggle` y filtro K cambiados de forma concurrente: ambos persistieron en
  serie, sin 409 ni reversión silenciosa;
- transferencia Waterfall → MASW repetida con la pestaña MASW ya montada:
  grupo activo ponderado, imagen `396×376` y estado “imagen combinada lista”;
- después de cambiar a Canchita en MASW, Waterfall, Agrupamiento, Enfase y
  Promedios retomaron Canchita y cargaron sus datos; el handoff fallido por
  falta de promedios conservó la solicitud con mensaje explícito y el mismatch
  se descartó con aviso;
- los cinco motores se pudieron elegir; un ADsurf ausente mostró su dependencia,
  mantuvo la ejecución bloqueada y conservó la elección tras recargar;
- la cabecera distinguió la instancia actual de la anterior que seguía en
  `8000`, y mostró PID y raíces efectivas;
- cuarentena real en navegador sobre sandbox: 2 carpetas desactivadas y luego
  restauradas, 3 archivos por carpeta todavía presentes, estado final
  `disabled: []`;
- consola del navegador sin warnings ni errores.
- gestos Canvas comprobados con puntero real del navegador integrado y raíces
  aisladas: trigger `0,0632→0,08549 s`, un pick reemplazado sin alterar el total
  `112→112`, y una región de cuatro vértices añadida `1→2`. El estado real de
  Canchita conservó su SHA-256 y en la raíz procesada real no se creó ningún
  archivo de anotaciones. Detalle reproducible en
  `server/audits/20260727_browser_canvas_acceptance.md`.
- grafo codebase-memory persistido como proyecto `TesisPythonFinal`; el trace
  entrante de `Pipeline.delete_folder()` devuelve `callers: []`.

La remediación adversarial completa está registrada en
`server/audits/20260727_fable_adversarial_remediation.md`.

## Matriz resumida de aceptación

| Área | Estado | Evidencia principal |
|---|---|---|
| Ingesta ZIP segura y reiniciable | OK | gate completo |
| Capturas, filtros y supresor armónico | OK | gate sintético + navegador |
| Agrupamiento, Enfase, Promedios | OK | gate + autoenfase en navegador |
| Waterfall, wiggle y exportaciones | OK | gate + persistencia visual |
| MASW JSON+NPZ, jobs y backends disponibles | OK | Canchita + E2E ZIP→Vs |
| Gestos Canvas trigger/pick/región | OK E2E | navegador sobre copia aislada + diff 1-a-1 |
| Cuarentena reversible sin pérdida | OK | hashes, 409 y ciclo UI completo |
| Windows directo | OK | arranque/puerto/meta |
| Contrato Docker | OK estructural | Dockerfile/compose/dependencias |
| Docker ejecutado en este host | Pendiente externo | no hay engine instalado |

## Cómo retomar

1. Leer este documento y `server/README.md`.
2. Usar el proyecto codebase-memory `TesisPythonFinal` y actualizar
   `.codebase-memory/graph.db.zst` si cambió el árbol. El índice anterior
   `TesisPython` quedó obsoleto durante esta sesión y no debe usarse como
   evidencia.
3. Ejecutar `python server/smoke_test.py`.
4. Levantar `python -m server --port 8000` y recorrer los ocho tabs con un
   sandbox antes de mutar datos reales.
5. En un host con Docker, ejecutar:

   ```powershell
   cd src/interfaces/python/server
   docker compose up --build -d
   docker compose ps
   Invoke-RestMethod http://localhost:8000/health
   ```

   Después repetir una ingesta, reiniciar el servicio durante un job y confirmar
   persistencia de los tres volúmenes.
6. No reintroducir borrado físico. El requisito vigente es cuarentena mediante
   bandera `__all__`; raw y ZIP son inmutables desde el tab.
7. No lanzar Geopsy ni escribir estados reales durante auditorías automatizadas
   sin una confirmación explícita.
