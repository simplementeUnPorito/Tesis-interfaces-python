# Aceptación E2E de gestos Canvas

Fecha: 2026-07-27  
Servidor: `http://127.0.0.1:8766/`, PID 18372  
Raw: `C:\Github\Tesis\data\raw`  
Processed aislado: `C:\Github\Tesis\tmp\browser_drag_audit\processed`  
Datos del servidor aislados: `C:\Github\Tesis\tmp\browser_drag_audit\server`

## Propósito y aislamiento

Esta corrida cierra la evidencia que no podía producir el controlador
semántico usado en la primera auditoría: arrastre real de trigger, arrastre real
de pick y dibujo de una región sobre Canvas.

Antes de iniciar se copió la campaña histórica Canchita a la raíz procesada
temporal. No se apuntó ninguna escritura a `data\processed`. El encabezado de la
propia aplicación confirmó las tres raíces efectivas y el PID del servidor de
prueba.

## Pick MASW

1. Se abrió MASW/Canchita y se calculó la dispersión combinada:
   `1 grupo(s) · 114×1201 · dx 2,00 m · L 26,00 m`.
2. Se eligió `Agregar/mover pick`.
3. Se arrastró con una trayectoria real de puntero el pick inicial:

   - eliminado: `[5.9377646062658975, 102.90000000000074]`;
   - añadido: `[6.083388754256371, 107.7666211063353]`.

4. El modo 0 conservó exactamente 112 picks. La comparación por pares exactos
   dio `removed=1`, `added=1`.
5. SHA-256 del estado temporal antes del arrastre:
   `1BD5317F2C197FE3184976E31024BF22E2982D980F7F2D819A8894942E8F87C2`.
6. SHA-256 después del arrastre:
   `01AAE8536F118A1B7E497617911720B856D1A0995DBECA696A7B2DF6B50520FB`.
7. El estado real de Canchita permaneció en
   `16F885FD0B8D97D692B532B416226B40280D887F45D67F492F967A1D4C8B3414`
   antes y después.

Captura visual:
`C:\Github\Tesis\tmp\browser_drag_audit\masw_pick_drag_after.png`.

## Región MASW

Se eligió `Dibujar región`, se marcaron cuatro puntos y se usó
`Cerrar región`. Las regiones de M0 pasaron de 1 a 2 sólo en la copia temporal.
La nueva región persistida fue:

```json
[
  [20.002729762728773, 89.82817533827318],
  [21.982406623304527, 89.82817533827318],
  [21.982406623304527, 109.8711315037049],
  [20.002729762728773, 109.8711315037049]
]
```

El SHA-256 temporal pasó a
`FB9123DB06379E891A992024BAB8A87DEF17996532598729E2037D086DBA53C7`;
el estado real conservó el hash citado arriba.

## Trigger de Capturas

1. Se abrió la captura raíz `Canchiga/001_actual`.
2. El trigger visible inicial era `0,0632 s (auto)`.
3. Se agarró la línea dentro de la tolerancia de 8 px y se arrastró 20 px.
4. La tabla se actualizó a `0,0855` y el archivo aislado persistió
   `trigger_s=0.08549009411177062` para el shot
   `7f249f8f29a52427`.
5. El archivo se creó únicamente en
   `C:\Github\Tesis\tmp\browser_drag_audit\processed\raw\field_review_annotations.json`.
   `C:\Github\Tesis\data\processed\raw\field_review_annotations.json` no existía
   antes y continuó sin existir después.

## Resultado

Los tres gestos Canvas requeridos funcionaron a través de los manejadores reales
de la interfaz y persistieron por las APIs del servidor. No se editó ningún
archivo histórico. Esta prueba es evidencia E2E registrada; el gate de consola
58/58 continúa siendo la evidencia automatizada independiente.
