
@echo off
echo Installing PyInstaller...
pip install pyinstaller

echo Cleaning up previous builds...
rmdir /s /q build
rmdir /s /q dist
del /q *.spec

echo Building Boshen Kai Line v3.2.4...
pyinstaller --noconfirm --onefile --windowed --name "BoshenKaiLine_v3.2.4" --hidden-import="rapidocr_onnxruntime" --collect-all="rapidocr_onnxruntime" main.py

echo Build Complete!
