@echo off
rem Levanta el servidor web de datos (FastAPI) en el puerto 8000.
rem Uso: dataServerUp.bat [--port 8001] [--read-only] ...
cd /d "%~dp0"
echo Servidor de datos en http://%COMPUTERNAME%:8000  (Ctrl+C para detener)
python -m server %*
if errorlevel 1 pause
