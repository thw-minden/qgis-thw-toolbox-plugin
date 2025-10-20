#!/bin/bash
# THW Toolbox Plugin Export Script - Shell Version
# ================================================

echo ""
echo "🚀 THW Toolbox Plugin Export Script"
echo "===================================="
echo ""

# Prüfe ob Python installiert ist
if ! command -v python3 &> /dev/null; then
    if ! command -v python &> /dev/null; then
        echo "❌ Fehler: Python ist nicht installiert oder nicht im PATH verfügbar"
        echo ""
        echo "Bitte installieren Sie Python von https://python.org"
        echo "und stellen Sie sicher, dass es im PATH verfügbar ist."
        echo ""
        read -p "Drücken Sie Enter zum Beenden..."
        exit 1
    else
        PYTHON_CMD="python"
    fi
else
    PYTHON_CMD="python3"
fi

# Führe das Python-Script aus
echo "📦 Starte Export..."
echo ""

$PYTHON_CMD export_script.py "$@"

# Prüfe den Exit-Code
if [ $? -ne 0 ]; then
    echo ""
    echo "💥 Export fehlgeschlagen!"
    echo ""
    read -p "Drücken Sie Enter zum Beenden..."
    exit 1
else
    echo ""
    echo "✅ Export erfolgreich abgeschlossen!"
    echo ""
    read -p "Drücken Sie Enter zum Beenden..."
fi
