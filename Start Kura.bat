@echo off
rem ---------------------------------------------------------------------------
rem  Kura launcher for Windows
rem
rem  First run: installs "uv" (Python manager) if needed, creates a private
rem  Python environment in %LOCALAPPDATA%\Kura and installs the libraries.
rem  Later runs start immediately.  "Start Kura.bat debug" keeps a console.
rem  Nothing is written into this (Google Drive) folder.
rem ---------------------------------------------------------------------------
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title Kura
cd /d "%~dp0"
set "APP_DIR=%~dp0"
if not defined KURA_HOME set "KURA_HOME=%LOCALAPPDATA%\Kura"
set "VENV=%KURA_HOME%\venv"
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "REQ=%APP_DIR%requirements.txt"
set "STAMP=%VENV%\.kura-requirements.txt"
set "PYTHONPYCACHEPREFIX=%KURA_HOME%\pycache"
if not defined KURA_PYTHON set "KURA_PYTHON=3.12"
if not defined KURA_PY_PREF set "KURA_PY_PREF=only-managed"
if not exist "%KURA_HOME%\logs" mkdir "%KURA_HOME%\logs"

echo ==============================================
echo   Kura - starting
echo ==============================================

rem --- 1. uv ------------------------------------------------------------------
set "UV="
for /f "delims=" %%i in ('where uv 2^>nul') do if not defined UV set "UV=%%i"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV (
  echo [1/3] Installing uv ^(https://astral.sh/uv^) ...
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
)
if not defined UV (
  echo ERROR: uv could not be installed. Check the internet connection.
  pause
  exit /b 1
)
echo [1/3] uv: !UV!
set "KURA_UV=!UV!"

rem --- 2. Python environment ------------------------------------------------------
if not exist "%PY%" (
  echo [2/3] Creating the Python environment ^(first run only^) ...
  "!UV!" venv --python %KURA_PYTHON% --python-preference %KURA_PY_PREF% --seed "%VENV%"
  if errorlevel 1 (
    echo ERROR: could not create the Python environment.
    pause
    exit /b 1
  )
  if exist "%STAMP%" del "%STAMP%"
) else (
  echo [2/3] Python environment: OK
)

rem --- 3. Libraries ---------------------------------------------------------------
fc /b "%REQ%" "%STAMP%" >nul 2>&1
if errorlevel 1 (
  echo [3/3] Installing libraries ^(takes a minute the first time^) ...
  "!UV!" pip install --python "%PY%" -r "%REQ%"
  if errorlevel 1 (
    echo ERROR: library installation failed. Check the internet connection.
    pause
    exit /b 1
  )
  copy /y "%REQ%" "%STAMP%" >nul
) else (
  echo [3/3] Libraries: OK
)

rem --- Launch ----------------------------------------------------------------------
if /i "%~1"=="debug" (
  "%PY%" -m kura
  pause
  exit /b 0
)
echo Launching Kura ...
start "" "%PYW%" -m kura
exit /b 0
