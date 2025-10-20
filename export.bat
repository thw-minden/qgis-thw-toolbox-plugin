@echo off
REM THW Toolbox Plugin Export Script - Windows Batch Version
REM ========================================================

echo.
echo 🚀 THW Toolbox Plugin Export Script
echo ====================================
echo.

REM Prüfe ob Python installiert ist
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Fehler: Python ist nicht installiert oder nicht im PATH verfügbar
    echo.
    echo Bitte installieren Sie Python von https://python.org
    echo und stellen Sie sicher, dass es im PATH verfügbar ist.
    echo.
    pause
    exit /b 1
)

REM Führe das Python-Script aus
echo 📦 Starte Export...
echo.

python export_script.py %*

REM Prüfe den Exit-Code
if errorlevel 1 (
    echo.
    echo 💥 Export fehlgeschlagen!
    echo.
    pause
    exit /b 1
) else (
    echo.
    echo ✅ Export erfolgreich abgeschlossen!
    echo.
    pause
)
