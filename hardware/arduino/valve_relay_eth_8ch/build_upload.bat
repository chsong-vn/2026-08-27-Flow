@echo off
rem ===========================================================================
rem  FlowChem 3-Way Valve firmware - build / upload helper
rem  Board : Waveshare ESP32-S3-ETH-8DI-8RO  (ESP32S3 Dev Module, USB CDC On)
rem
rem  ASCII-only on purpose: a .bat holding Korean text breaks cmd.exe's parser
rem  (UTF-8 + "chcp 65001" corrupts line parsing -> 'cho' is not recognized).
rem  Keep every message in this file plain ASCII.
rem ===========================================================================
setlocal
title ESP32 3-Way Valve Firmware - Build / Upload

set "CLI=C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
set "FQBN=esp32:esp32:esp32s3:CDCOnBoot=cdc"
set "SKETCH=%~dp0."

echo ===========================================================
echo  FlowChem 3-Way Valve (ESP32-S3-ETH-8DI-8RO) firmware
echo ===========================================================
echo.

if not exist "%CLI%" (
    echo [ERROR] arduino-cli not found at:
    echo         %CLI%
    echo         Check that Arduino IDE 2.x is installed.
    goto :end
)

echo [1/3] Compiling ... (board: ESP32S3 Dev Module, USB CDC On Boot)
echo.
"%CLI%" compile --fqbn "%FQBN%" "%SKETCH%"
if errorlevel 1 (
    echo.
    echo [FAILED] Compile error. Read the messages above.
    goto :end
)
echo.
echo [OK] Compile succeeded.
echo.

echo [2/3] Detecting connected boards ...
echo.
"%CLI%" board list
echo.

echo [3/3] Upload
echo   Connect the board over USB, then type its COM number below.
echo   Press ENTER on an empty line to stop after the compile check.
echo.
set "PORT="
set /p "PORT=COM port to upload (e.g. COM8) : "

if "%PORT%"=="" (
    echo.
    echo Upload skipped - compile verification only.
    goto :end
)

echo.
echo Uploading to %PORT% ...
echo.
"%CLI%" upload --fqbn "%FQBN%" -p %PORT% "%SKETCH%"
if errorlevel 1 (
    echo.
    echo [FAILED] Upload error.
    echo   - Check the COM number.
    echo   - Close anything holding the port (serial monitor, the main app).
    echo   - If the board needs boot mode: hold BOOT, tap RESET, release BOOT.
    goto :end
)
echo.
echo [OK] Upload done. The board reboots with every relay OFF (position 1).
echo      Next: run test_esp32_valve_live.py to check each channel.

:end
echo.
pause
endlocal
