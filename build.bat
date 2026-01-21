@echo off
echo Building BoshenTools...
if exist "tubiao.ico" (
    pyinstaller --noconfirm --onefile --windowed --name "BoshenTools" --icon="tubiao.ico" main.py
) else (
    pyinstaller --noconfirm --onefile --windowed --name "BoshenTools" main.py
)
echo Build complete. Check the 'dist' folder.
pause
