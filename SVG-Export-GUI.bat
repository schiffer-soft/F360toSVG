@echo off
rem Startet die GUI fuer den Fusion 360 -> SVG Export (ohne Konsolenfenster).
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 gui.py
) else (
    start "" py -3 gui.py
)
