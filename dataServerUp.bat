@echo off
rem Levanta el servidor web de datos (FastAPI) en el puerto 8000.
rem Uso: dataServerUp.bat [--port 8001] [--read-only] ...
rem Si el puerto esta ocupado solo por conexiones TIME_WAIT (recien bajado el
rem servidor anterior), reintenta cada 5 s hasta 2 minutos. Si hay un servidor
rem escuchando de verdad, no reintenta.
setlocal enabledelayedexpansion
cd /d "%~dp0"
set PORT=8000
set PREV=
for %%A in (%*) do (
    if "!PREV!"=="--port" set PORT=%%A
    set PREV=%%A
)
set TRIES=0
:run
echo Servidor de datos en http://%COMPUTERNAME%:%PORT%  (Ctrl+C para detener)
python -m server %*
if not errorlevel 1 goto :eof
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
    echo Ya hay un servidor escuchando en el puerto %PORT%. Usa dataServerDown.bat.
    pause
    goto :eof
)
set /a TRIES+=1
if %TRIES% GEQ 24 (
    echo No se pudo arrancar tras 2 minutos.
    pause
    goto :eof
)
echo Puerto en TIME_WAIT o error al arrancar; reintento %TRIES%/24 en 5 s...
timeout /t 5 /nobreak >nul
goto run
