# Servidor web de adquisición y MASW

La web usa los mismos archivos `data/raw` y `data/processed` que
`geophone_scope`; no migra ni reescribe campañas al abrirlas.

El tab histórico **Borrado** no elimina datos: aplica una bandera reversible
que excluye carpetas del pipeline. Raw, ZIP y anotaciones siempre se conservan.

El registro detallado de arquitectura, decisiones, compatibilidad, pruebas y
pendientes para continuar en otra sesión está en
[`IMPLEMENTATION_AUDIT.md`](IMPLEMENTATION_AUDIT.md).

## Windows

Desde `src/interfaces/python`:

```powershell
python -m pip install -r server/requirements-core.txt
python -m server
```

Para habilitar todos los backends científicos:

```powershell
python -m pip install -r server/requirements-full.txt
```

El puerto predeterminado es `8000`. Puede cambiarse con `--port 8001` o
`TESIS_PORT=8001`. Si ya hay otro proceso escuchando, el comando devuelve
código 2 e indica cómo consultar `/api/meta` del servidor activo.

Las tres raíces se pueden fijar de forma independiente con `--raw-root`,
`--processed-root` y `--data-root` (esta última guarda ZIP y jobs del
servidor). Sus equivalentes son `TESIS_RAW_ROOT`, `TESIS_PROCESSED_ROOT` y
`TESIS_SERVER_DATA_ROOT`.

`TESIS_DATA_ROOT=D:\campaña` mueve por sí sola el árbol completo a
`D:\campaña\raw`, `D:\campaña\processed` y `D:\campaña\server`. Las variables
específicas anteriores tienen precedencia si esas raíces se montan en discos
distintos.

Para inspeccionar datos reales sin exponer mutaciones se puede arrancar con
`--read-only` o `TESIS_READ_ONLY=1`: GET/HEAD/OPTIONS siguen disponibles y
todo POST devuelve 405 antes de entrar a un router.

La ingesta acepta exclusivamente el contrato actual del master,
`geophone_scope_web_zip_v4`. `metadata.json` es deliberadamente estricto:
minúsculas, nombre exacto y sin entradas duplicadas; puede estar en la raíz
como en el ZIP real del ESP o bajo una única carpeta contenedora. Sus cotas de
producción se pueden reducir sin cambiar el protocolo mediante
`TESIS_MAX_UPLOAD_BYTES`,
`TESIS_MAX_ZIP_FILES`, `TESIS_MAX_UNCOMPRESSED_BYTES` y
`TESIS_MAX_COMPRESSION_RATIO`. Los valores inválidos impiden el arranque en vez
de dejar el servidor sin límite; esto incluye NaN e infinito.

Para una prueba aislada no alcanza con cambiar sólo `--raw-root`: se debe pasar
también `--processed-root`. Así las anotaciones, grupos y estados MASW de la
prueba no pueden caer en el `data/processed` de trabajo.

## Docker

Desde `src/interfaces/python/server`:

```powershell
docker compose up --build
```

El contenedor corre como usuario sin privilegios, expone `8000`, comprueba
`/health` y monta de forma explícita `data/raw`, `data/processed` y
`data/server`. La cola de ingesta y la cola científica persisten en
`data/server`; las inversiones se ejecutan de a una en procesos hijos
cancelables y no bloquean la recepción de nuevos ZIP.

Geopsy no se instala en la imagen: el servidor siempre puede exportar sus
archivos y sólo intenta abrir la aplicación automáticamente en Windows
cuando existe una instalación local.
