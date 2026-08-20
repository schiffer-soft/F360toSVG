@echo off
rem ============================================================
rem  Fusion 360 -> SVG Export per Doppelklick
rem  Ansicht wird automatisch aus der Fusion-Kamera abgeleitet.
rem  Voraussetzung: Fusion 360 laeuft, MCP Server aktiviert.
rem  Das SVG landet in diesem Ordner (<Dokumentname>[-ansicht].svg).
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"
title Fusion 360 zu SVG

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 export_svg.py %*
) else (
    python export_svg.py %*
)

echo.
if %errorlevel%==0 (
    echo Fertig. Fenster mit beliebiger Taste schliessen.
) else (
    echo Export fehlgeschlagen - Meldung oben lesen.
)
pause >nul
