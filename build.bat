@echo off
echo Building BoshenTools...
if exist "tubiao.ico" (
    pyinstaller --noconfirm --onefile --windowed --name "BoshenTools" --icon="tubiao.ico" --collect-all "rapidocr_onnxruntime" main.py
) else (
    pyinstaller --noconfirm --onefile --windowed --name "BoshenTools" --collect-all "rapidocr_onnxruntime" main.py
)
echo Build complete. Check the 'dist' folder.
pause
