@echo off
chcp 65001 >nul
title 视频下载工具
cd /d "%~dp0"

:: 查找 Python（py 启动器最可靠）
set PYTHON=
for %%p in (py python python3) do (
    where %%p >nul 2>nul
    if not errorlevel 1 (
        set PYTHON=%%p
        goto found
    )
)

:: 尝试常见路径
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

echo [错误] 未找到 Python，请安装后重试
echo 下载地址: https://www.python.org/downloads/
pause
exit /b 1

:found
echo 正在启动视频下载工具...
%PYTHON% main.py
if errorlevel 1 (
    echo.
    echo ========================================
    echo 启动失败，请检查依赖是否安装:
    echo   pip install -r requirements.txt
    echo ========================================
    pause
)
