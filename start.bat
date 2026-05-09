@echo off
chcp 65001 >nul
title 视频去水印下载工具
python "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo 发生错误，请确保已安装依赖: pip install -r requirements.txt
    pause
)
