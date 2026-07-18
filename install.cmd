@echo off
rem Agent Keylight -- double-click to install (or: install.cmd --uninstall).
rem If Python is missing, it is installed automatically via winget.
setlocal

where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0installer.py" %*
    goto :end
)

echo Python not found -- installing it automatically (winget)...
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo.
    echo Automatic install failed. Please install Python from python.org,
    echo then run install.cmd again.
    start https://www.python.org/downloads/
    goto :end
)

rem this session's PATH predates the install -- locate python directly
set "PY="
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do set "PY=%%D\python.exe"
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do set "PY=%%D\python.exe"
if not defined PY (
    echo Python was installed -- please run install.cmd once more to finish.
    goto :end
)

"%PY%" "%~dp0installer.py" %*

:end
echo.
pause
