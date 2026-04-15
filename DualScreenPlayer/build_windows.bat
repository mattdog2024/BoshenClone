@echo off
echo ========================================
echo  双屏播放器 - Windows 打包脚本
echo ========================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

REM 安装依赖
echo 正在安装依赖...
pip install PyQt5 python-vlc Pillow mutagen pyinstaller

REM 检查 VLC 是否安装
if not exist "C:\Program Files\VideoLAN\VLC\libvlc.dll" (
    if not exist "C:\Program Files (x86)\VideoLAN\VLC\libvlc.dll" (
        echo.
        echo 警告：未找到 VLC 播放器！
        echo 请先安装 VLC 播放器：https://www.videolan.org/vlc/
        echo 安装完成后再运行此脚本
        pause
        exit /b 1
    )
)

REM 开始打包
echo.
echo 正在打包...
pyinstaller --noconfirm --onedir --windowed ^
    --name "DualScreenPlayer" ^
    --add-data "resources;resources" ^
    --hidden-import vlc ^
    --hidden-import PyQt5.QtCore ^
    --hidden-import PyQt5.QtGui ^
    --hidden-import PyQt5.QtWidgets ^
    main.py

if errorlevel 1 (
    echo.
    echo 打包失败！请检查错误信息
    pause
    exit /b 1
)

echo.
echo ========================================
echo  打包完成！
echo  输出目录: dist\DualScreenPlayer\
echo  可执行文件: dist\DualScreenPlayer\DualScreenPlayer.exe
echo ========================================
pause
