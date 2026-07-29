# Incidente UI Waterfall → MASW

Fecha: 2026-07-26  
Reporte: “Enviar a MASW” no calcula, no se puede elegir motor de inversión y
`wiggle` no responde.

## Diagnóstico

Había dos causas independientes:

1. El proceso que escuchaba en `0.0.0.0:8000` era `python.exe`, PID `22992`,
   iniciado a las 18:52. Respondía `/health`, pero `/api/meta` devolvía `404`;
   por definición era anterior a la versión actual.
2. En la SPA actual existía una carrera real. Si MASW ya estaba montado y se
   volvía desde Waterfall, `picker.reload()` actualizaba el `<select>` pero no
   la variable local `campaign`. `consumeWaterfallRequest()` comparaba contra
   esa campaña vieja y quitaba la solicitud de `localStorage`, por lo que el
   cálculo no arrancaba.

Además:

- los motores sin dependencias instaladas se renderizaban como opciones
  `disabled`; no se podían seleccionar para ver su diagnóstico;
- Waterfall esperaba la reconstrucción HTTP completa antes de aplicar el
  cambio visual de `wiggle`, por lo que el click parecía no hacer nada.

## Correcciones

- `masw.js` asigna a `campaign` el valor devuelto por `picker.reload()` antes de
  `loadAll()`.
- La solicitud Waterfall→MASW se elimina sólo después de confirmar campaña y
  antigüedad; el cálculo devuelve éxito/error y el flujo automático no continúa
  si la dispersión falló.
- Todos los backends son elegibles. Un backend no instalado muestra el comando
  o dependencia faltante y mantiene deshabilitado únicamente “Correr
  inversión”. La elección se persiste con revisión optimista.
- `waterfall.js` aplica el patch de vista de forma optimista, redibuja en el
  mismo click y revierte si el servidor rechaza la escritura.
- La cabecera consulta `/api/meta`: una instancia actual muestra versión,
  commit, PID y raíces; una instancia anterior muestra “servidor anterior ·
  reiniciar”.

## Aislamiento descubierto durante la reproducción

Una ejecución manual que pasaba sólo `--raw-root` conservaba la raíz global de
`field_review_data` para los estados procesados. Dos raw llamados `raw` podían
terminar compartiendo `data/processed/raw`.

Se añadió `--processed-root` / `TESIS_PROCESSED_ROOT`. `server.app` fija la
variable antes de importar la capa compartida y `/api/meta` informa la ruta
efectiva. El smoke test pasa las tres raíces explícitamente.

Durante el hallazgo se crearon dos estados exclusivamente sintéticos en
`C:\Github\Tesis\data\processed\raw`. Sus fechas de creación coincidían con la
prueba y el estado previo de anotaciones había sido `missing`. No se borraron:
se movieron a
`C:\Github\Tesis\tmp\browser_final_20260726_2127\smoke_sandbox\misdirected_state_20260726_2252`.
El estado anterior del sandbox también se conservó en
`pre_reproduction_state_20260726_2258`. Al terminar, `data/processed/raw` no
contenía esos archivos.

## Evidencia en navegador

Servidor actual aislado: `127.0.0.1:8765`, seis trazas validadas a
2, 4, 6, 8, 10 y 12 m.

- `/api/meta.roots.processed`:
  `...\smoke_sandbox\processed`.
- `wiggle` on/off produjo capturas distintas:
  - on: `2290d0642964926c4dcc245cca9cb5304c99913b4ea65f7576c44b78b0c8f408`
  - off: `1386a1e5b7cbc2c3df1a3855f3512dc4c989741c48151d501d7955093e82713d`
- “Enviar a MASW” dejó peso `1` en grupo 1, `0` en grupo 2 y terminó con:
  `imagen combinada lista`, `396×376`, `dx 2.00 m`, `L 10.00 m`.
- El selector mostró los cinco backends. Se seleccionó ADsurf aunque no estaba
  instalado, mostró `falta: submodulo third-party/ADsurf + pip install torch`,
  mantuvo “Correr inversión” deshabilitado y persistió `backend=adsurf` tras
  recargar.
- En `8765` la cabecera mostró `v2`, commit, PID y raíces. En el proceso viejo
  de `8000` mostró la advertencia de reinicio.

## Auditoría externa

Se invocó:

```text
claude -p --model fable --effort medium <auditoría adversarial acotada>
```

La CLI permaneció más de cuatro minutos sin emitir salida parcial ni dictamen.
Se detuvo únicamente ese proceso (`claude.exe`, PID `17768`) y no se atribuye a
Fable ninguna aprobación. La validación de este incidente se basa en el gate
local y en la reproducción de navegador documentada arriba.
