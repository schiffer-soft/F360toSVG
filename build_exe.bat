@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Erst pruefen, ob alle Pakete da sind: fehlt z.B. vtracer, baut
rem PyInstaller klaglos eine EXE ohne Vektor-Texturen.
echo Pruefe Build-Umgebung ...
py -3 check_build_env.py
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
echo.

rem Version aus gui.py (APP_VERSION) holen -> Dateiname wie beim Release
for /f "usebackq tokens=*" %%v in (`py -3 -c "import gui;print(gui.APP_VERSION)"`) do set VERSION=%%v
if "%VERSION%"=="" (
  echo FEHLER: APP_VERSION konnte nicht aus gui.py gelesen werden.
  pause
  exit /b 1
)

echo Baue F360toSVG-%VERSION%-portable.exe ^(PyInstaller, eine Datei^) ...
py -3 -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name "F360toSVG-%VERSION%-portable" ^
  --icon F360toSVG.ico ^
  --add-data "gui.html;." ^
  --add-data "fusion_extract.py;." ^
  gui.py
if errorlevel 1 (
  echo.
  echo BUILD FEHLGESCHLAGEN
  pause
  exit /b 1
)

echo.
echo Fertig: dist\F360toSVG-%VERSION%-portable.exe
pause
