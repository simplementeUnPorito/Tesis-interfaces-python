# Auditoría Fable — cuarentena reversible

Fecha: 2026-07-26  
Modelo: `claude --model fable`  
Alcance: sólo lectura, sin autorización para modificar archivos.

## Pregunta auditada

Se pidió revisar los caminos de desactivación/restauración en servidor, SPA y
estado compartido PyQt, buscando:

1. operaciones destructivas residuales;
2. carreras o revisiones incorrectas;
3. etapas científicas que incluyeran carpetas globalmente desactivadas;
4. problemas de restauración/UI;
5. incompatibilidades con estados históricos.

## Dictamen recibido

Fable concluyó que no quedaba ningún camino destructivo accesible:

- los `rmtree` restantes pertenecen sólo al staging temporal de ingesta;
- `Pipeline.delete_folder()` rechaza la operación;
- las rutas heredadas aplican la bandera y conservan el parámetro `zip` sólo
  como compatibilidad inerte;
- la SPA sólo llama a preview/disable/restore.

También confirmó que `__all__` se propaga a todos los grupos y que
`get_dataset()` excluye la cuarentena de Capturas, Filtros, Agrupamiento,
Enfase, Promedios, Waterfall, MASW y exportaciones.

Hallazgos:

| Severidad | Hallazgo de Fable |
|---|---|
| Media | Una ventana PyQt abierta podía conservar un snapshot antiguo de `alignment_disabled_folders.json` y, al guardar Enfase, pisar una bandera `__all__` creada desde la web. |
| Baja | El historial hacía read-modify-write sin mantener el mismo lock durante toda la operación. |
| Baja | Una bandera de una carpeta movida externamente podía quedar huérfana e imposible de restaurar desde la UI. |
| Baja | El proyector PyQt no reconocía la clave histórica web `#gN`. |

## Resolución aplicada

- Se añadió `frd.disabled_folders_lock()`: lockfile reentrante y entre procesos,
  compartido por web y PyQt.
- `save_disabled_folders()` relee y preserva `__all__` bajo ese lock cuando
  guarda un snapshot de Enfase; sólo la operación explícita de cuarentena usa
  `replace_global=True`.
- El servidor toma el mismo lock durante revisión, lectura, mutación y escritura.
- `record_deactivation()` mantiene un lock durante todo el append atómico.
- El catálogo crea filas `desactivada + ausente` para banderas huérfanas y
  permite restaurarlas aunque la carpeta ya no exista.
- El proyector PyQt ahora entiende `#gN`, igual que el servidor.

## Evidencia posterior

El check `borrado.preview_confirmacion` simula:

- una segunda app que mantiene el lock desde otro proceso;
- una escritura PyQt con snapshot viejo sin `__all__`;
- desactivación y restauración;
- preview obsoleta → `409`;
- exclusión de Capturas, Agrupamiento y Promedios;
- hashes raw y ZIP intactos;
- historial con `disable`/`restore` y `deleted: []`.

Resultado focal:

```text
python -m server.smoke_test --only borrado. --require borrado.
1/1 checks OK
```
