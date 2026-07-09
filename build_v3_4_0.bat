@echo off
chcp 65001 > nul
echo ============================================
echo  波神凯线系统 v3.4.0 构建脚本
echo  新功能: 系统托盘 + 全局热键
echo ============================================
echo.

echo [1/4] 安装依赖...
pip install pyinstaller keyboard --quiet

echo [2/4] 清理旧构建...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist BoshenKaiLine_v3.4.0.spec del /q BoshenKaiLine_v3.4.0.spec

echo [3/4] 开始打包...
pyinstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name "BoshenKaiLine_v3.4.0" ^
  --icon "boshen_tray.ico" ^
  --add-data "boshen_tray.ico;." ^
  --hidden-import="rapidocr_onnxruntime" ^
  --collect-all="rapidocr_onnxruntime" ^
  --hidden-import="keyboard" ^
  main.py

echo [4/4] 构建完成！
echo.
echo 输出文件: dist\BoshenKaiLine_v3.4.0.exe
echo.
echo 使用说明:
echo   - 程序启动后自动缩到右下角托盘
echo   - 双击托盘图标: 显示/隐藏面板
echo   - Ctrl+Alt+S  : 激活「单」画线工具
echo   - Ctrl+Alt+D  : 快速清除所有画线
echo   - 面板上「_」 : 隐藏到托盘
echo   - 面板上「X」 : 退出程序
echo.
pause
