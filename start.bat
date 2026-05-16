@echo off
title Video Downloader
cd /d "%~dp0"

:: Find Python
set PYTHON=
for %%p in (py python python3) do (
    where %%p >nul 2>nul
    if not errorlevel 1 (
        set PYTHON=%%p
        goto found
    )
)

:: Try common install paths
for %%d in (
    "C:\Python311" "C:\Python310" "C:\Python3"
    "%LOCALAPPDATA%\Programs\Python\Python311"
    "%LOCALAPPDATA%\Programs\Python\Python310"
    "%APPDATA%\Python\Python311"
) do (
    if exist "%%~d\python.exe" (
        set PYTHON="%%~d\python.exe"
        goto found
    )
)

echo [ERROR] Python not found. Please install Python first:
echo https://www.python.org/downloads/
pause
exit /b 1

:found
echo Starting video downloader...
%PYTHON% main.py
if errorlevel 1 (
    echo.
    echo ========================================
    echo Startup failed. Please install dependencies:
    echo   pip install -r requirements.txt
    echo ========================================
    pause
)
