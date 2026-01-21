# Boshen Kai Line (波神凯线) - Clone & Enhancement

A specialized stock analysis overlay tool that replicates the logic of "Boshen Kai Line" measurements, featuring automatic price axis recognition, K-line snapping, and multi-timeframe strategy support.

## 🚀 Key Features (v3.1.0)

### 1. Smart K-Line Detection
- **Auto-Measure**: Click any candle to automatically detect its High/Low.
- **Improved Sensitivity**: Enhanced algorithm (v3.0) detects thin wicks and snaps to the candle correctly, avoiding interfering objects like text labels or volume bars.
- **Smart Snap**: Intelligently prefers objects closer to your mouse click, preventing accidental jumps to distant chart elements.

### 2. Intelligent Calibration (Ruler)
- **OCR-Based Auto-Calibration**: Automatically scans the screen (Left & Right strips) to find the Price Axis.
- **Robustness**: Uses a **Density Filter** to distinguish between the dense Order Book (Level 2) and the sparse Price Axis, ensuring accurate readings even when zooming or panning.
- **Async Processing**: Calibration runs in the background, ensuring **Zero UI Freeze** when clicking tools.

### 3. Workflow & Persistence
- **Preset System**: Save your analysis configurations.
- **Ruler Persistence**: Saving a preset now SAVES the Calibration Scale. You can switch timeframes, load a preset, and immediately measure new K-lines without re-calibrating.

### 4. Technical Stack
- **Python 3.11** + **PySide6** (Qt)
- **RapidOCR-ONNXRuntime** for screen analysis
- **Async/Threading** for non-blocking UI

## 🛠 Installation & Usage

### Method 1: Portable Executable (Recommended)
Download the latest `BoshenKaiLine_v3.1.0.exe` from the Releases page.
1. Run the `.exe`.
2. A toolbar will appear at the top.
3. Open your Stock Trading Software (e.g., Tonghuashun, EastMoney).
4. Click **K-line** on the toolbar and click on a Red/Green candle to measure.

### Method 2: Running form Source
1. Clone the repository:
   ```bash
   git clone https://github.com/mattdog2024/BoshenClone.git
   cd BoshenClone
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   (Key deps: `PySide6`, `rapidocr_onnxruntime`, `numpy`, `opencv-python`)
3. Run:
   ```bash
   python main.py
   ```

## 📜 Shortcuts
- **Right Click**: Cancel current tool / Exit drawing mode.
- **Auto**: Force trigger screen calibration manually.

---
*Developed for personal analysis and educational purposes.*
