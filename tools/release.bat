@echo off
setlocal EnableExtensions

set "VER="
set "PASS="

:parse
if "%~1"=="" goto done

if /I "%~1"=="-ReleaseVersion" (
  set "VER=%~2"
  shift
  shift
  goto parse
)

rem Se ainda nao setou VER, assume que o 1o argumento e a versao
if "%VER%"=="" (
  set "VER=%~1"
  shift
  goto parse
)

rem Guarda os demais args (switches etc.)
set "PASS=%PASS% %1"
shift
goto parse

:done
if "%VER%"=="" (
  echo Uso:
  echo   tools\release.bat 0.1.5 [-NoPush] [-CreateRelease]
  echo   tools\release.bat -ReleaseVersion 0.1.5 [-NoPush] [-CreateRelease]
  echo.
  pause
  exit /b 1
)

set "SCRIPT=%~dp0release.ps1"
echo Chamando: "%SCRIPT%" "%VER%" %PASS%

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" "%VER%" %PASS%
set "ERR=%ERRORLEVEL%"

if not "%ERR%"=="0" (
  echo.
  echo Falhou com exit code %ERR%.
  echo.
  pause
)

exit /b %ERR%
