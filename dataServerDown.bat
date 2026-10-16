@echo off
rem Baja el servidor web de datos: mata el proceso que escucha en el puerto.
rem Uso: dataServerDown.bat [puerto]   (por defecto 8000)
setlocal
set PORT=%1
if "%PORT%"=="" set PORT=8000
set FOUND=0
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo Deteniendo PID %%P en el puerto %PORT%
    taskkill /F /PID %%P >nul
    set FOUND=1
)
if "%FOUND%"=="0" echo No hay nada escuchando en el puerto %PORT%.
