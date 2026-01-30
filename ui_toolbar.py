
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QFrame, QComboBox, QToolButton, QColorDialog)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QFont, QColor

class DraggableWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        # self.setAttribute(Qt.WA_TranslucentBackground) # Removed to fix "too transparent" issue
        self._drag_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
    
    def mouseReleaseEvent(self, event):
        self._drag_pos = None

class BoshenToolbar(DraggableWidget):
    """
    The main toolbar window looking like the screenshot.
    """
    tool_selected = Signal(str) # Emits the name of the tool selected
    clear_requested = Signal()
    close_requested = Signal()
    color_changed = Signal(QColor) # Emits the new color
    fast_mode_toggled = Signal(bool) # Emits whether fast mode is on/off
    timeframe_changed = Signal(str) # Emits the selected timeframe name
    save_requested = Signal() # Emits when "Save" button is clicked

    def __init__(self):
        super().__init__()
        self.init_ui()
    
    def init_ui(self):
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5) # Increased margins for easier grabbing
        main_layout.setSpacing(2)
        
        # Background style matching standard windows or the gray in screenshot
        self.setStyleSheet("""
            QWidget {
                background-color: #f0f0f0;
                border: 1px solid #a0a0a0;
                font-family: "SimSun";
                font-size: 12px;
            }
            QLabel#DragHandle {
                color: #888888;
                font-weight: bold;
                cursor: size_all; /* Hint that it's moveable */
            }
            QPushButton, QToolButton {
                border: 1px solid transparent;
                background-color: transparent;
                padding: 2px;
            }
            QPushButton:hover, QToolButton:hover {
                border: 1px solid #8f8f8f;
                background-color: #e0e0e0;
            }
            QPushButton:pressed, QToolButton:pressed {
                border: 1px solid #4f4f4f;
                background-color: #c0c0c0;
            }
            QToolButton:checked {
                background-color: #a0a0a0; 
                border: 1px inset #555555;
            }
        """)
        
        # Row 1
        row1 = QHBoxLayout()
        row1.setSpacing(2)
        
        # Drag Handle
        drag_handle = QLabel("::")
        drag_handle.setObjectName("DragHandle")
        row1.addWidget(drag_handle)
        
        # Icon placeholders (Text for now)
        # "Candles", "Vol", "Single", "Box", "Shadow", "Bar1", "Bar2", "Count", "Star", "Dx"
        btns_r1 = ["k线", "量", "单", "箱", "影", "画", "测2", "数", "米", "Dx", "自动"]
        for b_text in btns_r1:
            btn = QToolButton()
            btn.setText(b_text)
            if b_text == "Dx":
                btn.clicked.connect(self.clear_requested.emit)
            elif b_text == "自动":
                 btn.clicked.connect(lambda: self.on_tool_click("ocr_selection"))
            elif b_text == "画":
                 btn.clicked.connect(lambda: self.on_tool_click("free_draw"))
            else:
                btn.clicked.connect(lambda checked=False, t=b_text: self.on_tool_click(t))
            row1.addWidget(btn)
        
        # Line separator
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        row1.addWidget(line)
        
        # Color Box (Red)
        self.color_btn = QPushButton()
        self.color_btn.setStyleSheet("background-color: red; border: 1px solid gray; width: 40px;")
        self.color_btn.clicked.connect(self.choose_color)
        row1.addWidget(self.color_btn)
        
        # Dropdown
        self.combo = QComboBox() # Save reference to access later if needed
        self.combo.addItems(["日线", "4小时", "1小时"])
        self.combo.setFixedWidth(60) # Increased width to fit text
        self.combo.currentTextChanged.connect(self.timeframe_changed.emit)
        row1.addWidget(self.combo)

        # "Save" Button (Snapshot)
        save_btn = QToolButton()
        save_btn.setText("存") # Store/Save
        save_btn.setToolTip("保存当前屏幕线段到选定时区 (Save current drawings to selected Timeframe)")
        save_btn.clicked.connect(self.save_requested.emit)
        row1.addWidget(save_btn)
        
        # Help and Close
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setStyleSheet("color: blue; font-weight: bold;")
        row1.addWidget(help_btn)
        
        close_btn = QToolButton()
        close_btn.setText("X")
        close_btn.clicked.connect(self.close_requested.emit)
        row1.addWidget(close_btn)
        
        row1.addStretch()
        main_layout.addLayout(row1)
        
        # Horizontal Line
        h_line = QFrame()
        h_line.setFrameShape(QFrame.HLine)
        h_line.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(h_line)
        
        # Row 2
        row2 = QHBoxLayout()
        row2.setSpacing(2)
        
        # "Intro", "Table", "Fast"
        btns_r2_text = ["解", "表", "快"]
        for b_text in btns_r2_text:
            btn = QToolButton()
            btn.setText(b_text)
            if b_text == "快":
                btn.setCheckable(True)
                btn.toggled.connect(self.fast_mode_toggled.emit)
            row2.addWidget(btn)

        # Separator
        line2 = QFrame()
        line2.setFrameShape(QFrame.VLine)
        line2.setFrameShadow(QFrame.Sunken)
        row2.addWidget(line2)

        # Drawing tools icons (Lines, Rect, etc.)
        # Using text to represent shapes for now: \ \\ [] G % T ||| |||| / V ^ v
        draw_tools = ["\\", "\\\\", "[]", "G", "%", "T", "|||", "||||", "/", "V", "↑", "↓"]
        for dt in draw_tools:
            btn = QToolButton()
            btn.setText(dt)
            # Map specific interesting ones
            if dt == "V": # Fan
                btn.clicked.connect(lambda: self.on_tool_click("fan"))
            else:
                 btn.clicked.connect(lambda checked=False, t=dt: self.on_tool_click(t))
            row2.addWidget(btn)
            
        row2.addStretch()
        main_layout.addLayout(row2)
        
        self.setLayout(main_layout)

    def on_tool_click(self, tool_name):
        print(f"Tool selected: {tool_name}")
        self.tool_selected.emit(tool_name)

    def choose_color(self):
        color = QColorDialog.getColor(Qt.red, self, "Select Color")
        if color.isValid():
            # Update button style
            self.color_btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid gray; width: 40px;")
            self.color_changed.emit(color)

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication(sys.argv)
    win = BoshenToolbar()
    win.show()
    sys.exit(app.exec())
