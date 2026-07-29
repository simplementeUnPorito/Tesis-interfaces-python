# Auditoría Fable — autoenfase

Fecha: 2026-07-26  
Comando: `claude --model fable --print`

## Consulta

Se consultó la convención de signo y las salvaguardas mínimas para estimar un
offset de carpeta por correlación normalizada, usando
`t_mostrado = t_original + O` y muestreando la traza objetivo en `t_ref - O`.

## Dictamen recibido

Fable consideró correcta la ecuación y recomendó:

1. no aplicar offsets con correlación normalizada menor a aproximadamente
   0.6–0.7;
2. detectar saltos de ciclo mediante un segundo máximo cercano al principal;
3. verificar la plausibilidad física y no ocultar una correlación negativa.

También recomendó maximizar correlación con signo en vez del valor absoluto,
porque la polaridad se corrige por separado.

## Revisión crítica local

El ejemplo de signo incluido por Fable decía que una señal retrasada 10 ms debía
producir `O = +10 ms`. Eso contradice la ecuación: si
`target(t) = ref(t - 0.010)`, para que
`target(t_ref - O) = ref(t_ref)` se necesita `O = -0.010`.

La implementación se aceptó sólo después de que una prueba sintética HTTP
confirmó ese signo con una demora de 20 ms. Se adoptaron el umbral, el rechazo
por ambigüedad y la correlación con signo. Se descartó la sugerencia de usar
envolvente de Hilbert porque el contrato del proyecto limita Hilbert a
visualización y prohíbe que alimente el pipeline científico. La comprobación de
monotonicidad por distancia tampoco aplica directamente: el autoenfase compara
carpetas dentro del mismo label de distancia.
