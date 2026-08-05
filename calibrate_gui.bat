@echo off
REM ASCII only - Korean text breaks the cmd parser
cd /d "%~dp0"
py -3.14 calibrate_plate96_gui.py
if errorlevel 1 (
  echo.
  echo [FAILED] See the error above.
  pause
)
