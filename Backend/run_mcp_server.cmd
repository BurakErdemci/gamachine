@echo off
REM Gamachine — MCP Server launcher (Windows)
REM run_mcp_server.sh'in Windows karsiligi. Paketlenmis build: yanindaki
REM backend.exe'yi kullanir (venv/python gerekmez); dev: venv python'a duser.
setlocal
set "SCRIPT_DIR=%~dp0"
REM No "2>> log" redirection here: cmd.exe opens the file without write sharing,
REM so while one unityai server ran every other launch died before Python
REM started (measured 27 Sep 2026). The server opens this log itself, shared.
if not defined UNITYAI_MCP_LOG_FILE set "UNITYAI_MCP_LOG_FILE=%SCRIPT_DIR%mcp_server.log"
REM Turkce karakterler icin UTF-8 stdio (cp1252 UnicodeEncodeError fix).
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM Paketlenmis build: donmus backend.exe yanindaysa onu calistir.
if exist "%SCRIPT_DIR%backend.exe" (
  "%SCRIPT_DIR%backend.exe" mcp-server %*
  exit /b %ERRORLEVEL%
)

REM Dev: venv python, yoksa sistem python.
cd /d "%SCRIPT_DIR%"
set "PYTHON=%SCRIPT_DIR%venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
set "PYTHONPATH=%SCRIPT_DIR%app;%PYTHONPATH%"
"%PYTHON%" -m app.unity_ai_mcp.server %*
exit /b %ERRORLEVEL%
