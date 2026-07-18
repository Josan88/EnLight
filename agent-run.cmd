@echo off
rem Run any agent CLI with keylight status, e.g.:  agent-run codex ...
rem running while the command runs; on non-zero exit, error (solid) for 15s.
setlocal
set "SID=wrap-%RANDOM%%RANDOM%"
python "%~dp0lightctl.py" running %SID%
call %*
set EC=%ERRORLEVEL%
if not "%EC%"=="0" (
    python "%~dp0lightctl.py" error %SID%
    timeout /t 15 /nobreak >nul
)
python "%~dp0lightctl.py" clear %SID%
exit /b %EC%
