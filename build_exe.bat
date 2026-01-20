@echo off
setlocal
cd /d "%~dp0"

REM --- Detectar Python: tenta 'py' primeiro, depois 'python'
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if "%PY%"=="" (
  where python >nul 2>&1 && set "PY=python"
)

if "%PY%"=="" (
  echo [ERRO] Python nao encontrado.
  echo Instale Python 3 e marque "Add Python to PATH".
  pause
  exit /b 1
)

echo Usando: %PY%
echo.

REM --- Dependencias
%PY% -m pip install --upgrade pip
if errorlevel 1 goto :err

%PY% -m pip install --upgrade pyinstaller ttkbootstrap
if errorlevel 1 goto :err

REM --- Limpar builds anteriores
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q ScannerGUI.spec 2>nul

REM --- Build
REM --uac-admin faz o EXE pedir permissao de administrador ao abrir (UAC)
%PY% -m PyInstaller --noconsole --onefile --clean --uac-admin --name ScannerGUI gui_scanner_pro.py
if errorlevel 1 goto :err

echo.
echo Pronto! Seu exe esta em:
echo "%cd%\dist\ScannerGUI.exe"
pause
exit /b 0

:err
echo.
echo [ERRO] Falhou ao gerar o exe. Veja as mensagens acima.
pause
exit /b 1
