@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   MT Copy Trading System - Build
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found
    pause & exit /b 1
)

REM Check required packages
echo [*] Checking dependencies...
python -c "import MetaTrader5, zmq" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [*] Installing dependencies...
    pip install MetaTrader5 pyzmq >nul 2>&1
)

REM Clean
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo.
echo [1/2] Building MT_Copy_Trading.exe...
echo       This may take 1-2 minutes...

pyinstaller --onefile --noconsole ^
    --name "MT_Copy_Trading" ^
    --hidden-import zmq ^
    --hidden-import zmq.backend.cython ^
    --hidden-import MetaTrader5 ^
    --hidden-import tkinter ^
    --hidden-import tkinter.ttk ^
    --hidden-import tkinter.scrolledtext ^
    --hidden-import json ^
    --hidden-import threading ^
    --hidden-import queue ^
    --hidden-import argparse ^
    --hidden-import logging ^
    main.py >nul 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] PyInstaller build failed!
    echo Try running without redirect:
    echo   pyinstaller --onefile --noconsole --name MT_Copy_Trading main.py
    pause
    exit /b 1
)

echo.
echo [2/2] Creating distribution package...

REM Create dist package directory
set PKG=dist\MT_Copy_Trading_v1.0.0
mkdir "%PKG%" >nul 2>&1
mkdir "%PKG%\config" >nul 2>&1
mkdir "%PKG%\logs" >nul 2>&1

REM Copy exe and files
copy /Y "dist\MT_Copy_Trading.exe" "%PKG%\" >nul
copy /Y ".env.template" "%PKG%\" >nul
copy /Y "config\copy_config.yaml" "%PKG%\config\" >nul
copy /Y "README.txt" "%PKG%\" >nul
copy /Y "LICENSE.txt" "%PKG%\" >nul

REM Create launcher batch file
echo @echo off > "%PKG%\run.bat"
echo start "" "%%~dp0MT_Copy_Trading.exe" >> "%PKG%\run.bat"

echo.
echo ============================================
echo   Build Complete!
echo ============================================
echo.
echo   Output: %PKG%
echo.
echo   Files:
echo     MT_Copy_Trading.exe   - Main application
echo     .env.template          - Account config template
echo     config\                - System configuration
echo     logs\                  - Log output directory
echo     run.bat                - Quick launcher
echo     README.txt             - Documentation
echo.
echo   To distribute:
echo     1. Zip the MT_Copy_Trading_v1.0.0 folder
echo     2. Or run Inno Setup with installer.iss for .exe installer
echo.
echo   Size:
dir /s "%PKG%" | findstr "File(s)"
echo.
pause
