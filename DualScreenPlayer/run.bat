@echo off
chcp 65001 >nul
echo 正在启动双屏播放器...

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 Python！
    echo 请安装 Python 3.8+ 后再试
    pause
    exit /b 1
)

REM 检查依赖
python -c "import PyQt5, vlc" >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖，请稍候...
    pip install PyQt5 python-vlc Pillow mutagen
)

REM 启动软件
python main.py
