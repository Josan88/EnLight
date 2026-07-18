@echo off
rem Agent Keylight launcher -- safe to run repeatedly (daemon is single-instance).
rem If OpenRGB is installed (RGB keyboards), start its SDK server too.

for %%P in ("%ProgramFiles%\OpenRGB\OpenRGB.exe" "%LocalAppData%\Programs\OpenRGB\OpenRGB.exe") do (
    if exist %%P (
        tasklist /fi "imagename eq OpenRGB.exe" | find /i "OpenRGB.exe" >nul || start "" %%P --server --startminimized
    )
)

start "" /min pythonw "%~dp0daemon.py"
exit /b 0
