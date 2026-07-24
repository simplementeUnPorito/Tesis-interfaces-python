# Software Python de Tesis

Aplicaciones de adquisición, revisión de campo y análisis MASW. Este repositorio conserva el historial que antes vivía en `src/python` y puede ejecutarse sin clonar el firmware ni los modelos MATLAB.

## Componentes

- `geophone_scope/`: GUI PyQt6, conversión de capturas y revisión de datos de campo.
- `Calculos rapidos/`: utilidades numéricas.
- `Ordenar Obsidian/`: herramientas de mantenimiento de notas.
- `third-party/`: motores MASW externos como submódulos.

El paquete binario Geopsy para Windows se conserva en `third-party/geopsy/` mediante Git LFS. GitHub guarda sus punteros y los objetos viven en el folderstore privado `Github-LFS/repositories/Tesis-interfaces-python`.

## Instalación

```powershell
git clone --recurse-submodules https://github.com/simplementeUnPorito/Tesis-interfaces-python.git
cd Tesis-interfaces-python/geophone_scope
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

La aplicación busca datos en `$env:TESIS_DATA_ROOT`. Si no se define, usa el submódulo `data/` del superproyecto cuando existe y, en un clon independiente, `data/` dentro de este repositorio. La estructura esperada es `raw/` para capturas inmutables y `processed/` para resultados.

Para restaurar Geopsy después de clonar con `GIT_LFS_SKIP_SMUDGE=1`:

```powershell
$env:GITHUB_LFS_ROOT = 'C:\Users\elias\OneDrive\Github-LFS'
.\scripts\configure-lfs-folderstore.ps1
.\scripts\hydrate-lfs.ps1 -Include 'third-party/geopsy/**'
```
