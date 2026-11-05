@echo off
REM unityai CLI wrapper (Windows) — 'unityai' bash scriptinin karsiligi.
REM agy/CLI bunu run_command ile cagirir. Paketlenmis build: backend.exe;
REM dev: venv python.
setlocal
set "SCRIPT_DIR=%~dp0"
set "ORIG_PWD=%CD%"
REM Turkce karakterler icin UTF-8 stdio (cp1252 UnicodeEncodeError fix).
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM Backend's env file (UNITYAI_URL, WORKSPACE; the token has its own file),
REM kept in the per-user data folder, not beside this script.
set "ENV_FILE=%USERPROFILE%\.unity_architect_ai\.unityai_cli.env"
if exist "%ENV_FILE%" (
  for /f "usebackq eol=# tokens=1* delims==" %%a in ("%ENV_FILE%") do (
    if /i "%%a"=="UNITYAI_URL" set "%%a=%%b"
    if /i "%%a"=="WORKSPACE" set "%%a=%%b"
  )
)
if not defined WORKSPACE set "WORKSPACE=%ORIG_PWD%"

cd /d "%SCRIPT_DIR%"

REM Paketlenmis build: donmus backend.exe yanindaysa onu calistir.
if exist "%SCRIPT_DIR%backend.exe" (
  "%SCRIPT_DIR%backend.exe" unityai %*
  exit /b %ERRORLEVEL%
)

REM Dev: venv python, yoksa sistem python.
set "PYTHON=%SCRIPT_DIR%venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
set "PYTHONPATH=%SCRIPT_DIR%app;%PYTHONPATH%"
"%PYTHON%" -m app.unityai_cli %*
exit /b %ERRORLEVEL%
