# Remediación de auditorías Fable — 2026-07-27

Base auditada: `10d9fb8f77ec`, rama `cambios-red`, diff sin commit.

Este registro une las dos auditorías adversariales recibidas: flujo
Waterfall/MASW y seguridad de ingesta ZIP. No se eliminó ningún raw, ZIP,
estado histórico ni módulo legado.

## Integridad de campaña y UI

- P1 corregido en Waterfall, Agrupamiento, Enfase y Promedios. Cada `resume()`
  espera `picker.reload()`, reasigna la variable local `campaign` y recién
  entonces carga datos. Se retiraron las cargas iniciales con campaña vacía de
  Waterfall y Agrupamiento.
- MASW usa un token de secuencia en `loadAll()`: una respuesta lenta de una
  campaña anterior no puede ganar la carrera y reemplazar el estado visible.
- Prueba real en navegador: después de elegir Canchita en MASW, Waterfall,
  Agrupamiento, Enfase y Promedios mostraron Canchita y cargaron sus datos. El
  raw real fue sólo lectura; todos los estados se dirigieron a
  `tmp/browser_fable_remediation/processed`.

## Waterfall → MASW

- La solicitud ya no se elimina antes de calcular. Si la dispersión falla,
  permanece disponible y la UI explica que se conservará para reintentar.
- Las solicitudes vencidas, inválidas o de otra campaña se descartan de forma
  explícita y muestran el motivo; no pueden dispararse inesperadamente hasta
  dos minutos después.
- El flujo automático captura y muestra errores de auto-pick e inversión. Sólo
  consume la solicitud cuando la acción pedida quedó iniciada con éxito.
- Los reloads internos por 409 desactivan el consumo recursivo de la solicitud.
- Los errores científicos de geometría, recorte o ausencia de promedios ahora
  son 422. El código 409 queda reservado a conflictos de revisión.
- Prueba real en navegador: una campaña sin promedios informó la causa exacta y
  conservó el envío; cambiar luego a otra campaña lo descartó con aviso.

## Guardado de vista y polling

- Waterfall reemplazó la carrera de POST por una cola coalescente. La UI sigue
  respondiendo en el click, pero cada POST usa la revisión devuelta por el
  anterior. El mensaje de 409 se escribe después de recargar y ya no queda
  oculto por el guard de pedido.
- Prueba real en navegador: wiggle y filtro K cambiados concurrentemente
  quedaron ambos persistidos, con estado `guardado`.
- El polling MASW valida HTTP, conserva `currentJob` ante fallos transitorios,
  muestra reconexión y reintenta con backoff. Un error aislado ya no congela
  permanentemente los botones. El gate verifica el contrato fuente; no se
  forzó una caída de red real durante el navegador.
- El catálogo expone `can_launch`; el cliente ya no infiere la capacidad de
  abrir Geopsy buscando la palabra `launch` en un texto.

## Resultado de inversión con revisión obsoleta

- Perfil, curvas y configuración se escriben como artefactos antes de intentar
  fusionar el resultado con JSON+NPZ.
- Si la revisión cambió durante el cálculo, el job termina `listo` con
  `persisted=false` y `persistence_error`; no pisa el estado nuevo y no pierde
  minutos de CPU.
- Regresión ejecutada: una inversión se encoló detrás de otra, el estado MASW
  se modificó antes de persistir y el job conservó tres artefactos descargables.

## Raíces y gate de sólo lectura

- `TESIS_DATA_ROOT` ahora deriva por defecto `raw`, `processed` y `server`.
  Las variables y banderas específicas conservan precedencia.
- `--read-only` / `TESIS_READ_ONLY=1` rechaza todos los POST con 405.
- El servidor del gate que observa el raw/processed real usa ese modo.
- Regresión ejecutada: arrancar sólo con `TESIS_DATA_ROOT`, ingestar un ZIP y
  comprobar que ZIP/jobs/raw/processed quedan bajo la raíz aislada y que
  `data/server` real no cambia.

## ZIP y límites

- `_positive_float` rechaza NaN e infinito además de cero, negativos y texto.
- El traversal de prueba contiene un `metadata.json` v4 válido y exige el
  mensaje `ruta sospechosa`; ya no puede pasar por un rechazo estructural.
- La bomba exige específicamente `relación ... sospechosa`.
- Se prueba el layout real del ESP sin carpeta prefijo.
- Se prueba y rechaza BZIP2.
- Las rutas normalizadas duplicadas, también por diferencias de caja de
  Windows, se rechazan antes de leer metadata o extraer. Esto evita validar un
  `metadata.json` y extraer otro.
- `metadata.json` sigue siendo deliberadamente case-sensitive y exacto; quedó
  documentado.

## Lock compartido

La auditoría señaló una ventana pequeña al romper locks obsoletos. Se añadió un
token aleatorio de propiedad, comparación de contenido/mtime/tamaño antes de
romper un lock y verificación del token antes de liberarlo. La primera versión
del cambio dejó locks en Windows por traducción CRLF; el primer gate completo
lo detectó (`enfase.clave_compartida` y `borrado.preview_confirmacion`). Se
corrigió abriendo el lockfile con `O_BINARY`, se repitieron ambos checks y luego
el gate completo.

## Evidencia final

```text
python -m compileall -q server geophone_scope
python -m server.smoke_test --json C:\Github\Tesis\tmp\implementation_smoke_fable_remediated_final_20260727\results.json
58/58 checks OK
```

Log:
`C:\Github\Tesis\tmp\implementation_smoke_fable_remediated_final_20260727\logs\gate_20260727_000055.log`

Checks focalizados previos:

- remediación principal: 16/16;
- regresión de lock: 2/2;
- primer full gate: 56/58, conservado como evidencia del defecto intermedio.

## Riesgos que no se ocultan

- El runtime Docker sigue sin poder ejecutarse en este host porque no hay
  Docker, Podman, nerdctl ni distribución WSL. El contrato estructural sí pasa.
- Dos datasets de padres distintos y con el mismo basename todavía podrían
  compartir `processed/<basename>` si el operador los dirige deliberadamente a
  la misma raíz procesada. Cambiar ese layout rompería compatibilidad histórica;
  se mantiene la exigencia operativa de raíces procesadas distintas.
- El gate completo depende del fixture histórico local Canchita para su prueba
  de compatibilidad; en otra máquina hace falta proveer ese fixture o separar
  el perfil portable.
- Los gestos de trigger, pick y región se ejercieron después con puntero real
  del navegador integrado sobre una copia aislada y quedaron documentados en
  `20260727_browser_canvas_acceptance.md`. Esa aceptación E2E no forma parte
  todavía del gate de consola.
- `jobs.json` no tiene poda automática, coherente con la decisión actual de no
  aplicar retención destructiva. Si crece durante años, deberá añadirse un
  archivado explícito y reversible.
