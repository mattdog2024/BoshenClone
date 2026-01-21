from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QFrame, QButtonGroup, QRadioButton, QGraphicsOpacityEffect, QScrollArea)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont
from strategy_manager import StrategyManager, Timeframe

class StrategyWidget(QWidget):
    """
    Sidebar widget derived from QWidget.
    Provides strategy guidance and timeframe switching.
    """
    timeframe_changed = Signal(str) # Emits timeframe value (e.g. "日线")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = StrategyManager()
        
        # UI Setup
        self.setup_ui()
        self.refresh_ui()

    def setup_ui(self):
        self.setFixedWidth(260)
        self.setStyleSheet("""
            QWidget {
                background-color: rgba(30, 30, 30, 230);
                color: white;
                border-radius: 10px;
                font-family: "SimHei";
            }
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QWidget#ScrollContent {
                background-color: transparent;
            }
            QPushButton {
                background-color: #4CAF50; 
                border: none;
                padding: 8px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton#TabBtn {
                background-color: transparent;
                border: 1px solid #555;
                color: #AAA;
            }
            QPushButton#TabBtn:checked {
                background-color: #2196F3;
                color: white;
                border: 1px solid #2196F3;
            }
            QPushButton[selected="true"] {
                background-color: #FF9800; /* Orange for selected state */
                color: black;
                border: 2px solid #FFD700;
            }
            QLabel {
                font-size: 14px;
            }
            QLabel#Title {
                font-size: 16px;
                font-weight: bold;
                color: #FFD700;
            }
            QLabel#Guidance {
                color: #DDD;
                font-size: 12px;
                padding: 5px;
            }
            /* Vertical Scrollbar styling */
            QScrollBar:vertical {
                border: none;
                background: rgba(0,0,0,50);
                width: 8px;
                margin: 0px 0px 0px 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,100);
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        
        # State for dragging
        self.drag_start_pos = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 20, 15, 20)

        # 1. Header (Cursor hint for dragging)
        header_lbl = QLabel("波神策略向导 (按住拖动)", self)
        header_lbl.setObjectName("Title")
        header_lbl.setAlignment(Qt.AlignCenter)
        header_lbl.setCursor(Qt.SizeAllCursor)
        layout.addWidget(header_lbl)

        layout.addSpacing(15)

        # 2. Timeframe Tabs
        tf_layout = QHBoxLayout()
        self.tf_group = QButtonGroup(self)
        self.tf_group.setExclusive(True)
        
        timeframes = [Timeframe.DAILY, Timeframe.H4, Timeframe.H1, Timeframe.M5]
        self.tf_buttons = {}

        for tf in timeframes:
            btn = QPushButton(tf.value)
            btn.setCheckable(True)
            btn.setObjectName("TabBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, t=tf: self.on_tf_clicked(t))
            self.tf_group.addButton(btn)
            tf_layout.addWidget(btn)
            self.tf_buttons[tf.value] = btn
        
        layout.addLayout(tf_layout)
        layout.addSpacing(20)

        # 3. Status / Guidance Area (Scrollable)
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(150)
        
        content_widget = QWidget()
        content_widget.setObjectName("ScrollContent")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0,0,0,0)
        
        self.guidance_lbl = QLabel("", content_widget)
        self.guidance_lbl.setObjectName("Guidance")
        self.guidance_lbl.setWordWrap(True)
        self.guidance_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        content_layout.addWidget(self.guidance_lbl)
        
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area)

        layout.addSpacing(10)

        # 4. Contextual Actions (Dynamic based on logic)
        self.action_layout = QVBoxLayout()
        layout.addLayout(self.action_layout)
        
        # 5. Analysis Button
        self.analyze_btn = QPushButton("开始智能分析 (Smart Analysis)")
        self.analyze_btn.setStyleSheet("""
            QPushButton {
                background-color: #673AB7;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
                border: 2px solid #512DA8;
            }
            QPushButton:hover {
                background-color: #5E35B1;
            }
        """)
        self.analyze_btn.clicked.connect(self.request_analysis)
        layout.addWidget(self.analyze_btn)

        layout.addStretch()
        
        # 6. Reset / Footer
        reset_btn = QPushButton("重置策略状态")
        reset_btn.setStyleSheet("background-color: #d32f2f;")
        reset_btn.clicked.connect(self.reset_strategy)
        layout.addWidget(reset_btn)

    def refresh_ui(self):
        # Update Tab Selection
        current_tf = self.manager.get_current_timeframe()
        if current_tf.value in self.tf_buttons:
            self.tf_buttons[current_tf.value].setChecked(True)
        
        # Update Guidance Text
        self.guidance_lbl.setText(self.manager.get_guidance_text())

        # Update Dynamic Buttons
        self.update_dynamic_actions(current_tf)


    def on_tf_clicked(self, timeframe):
        print(f"Switching to {timeframe.value}")
        self.manager.set_timeframe(timeframe)
        self.refresh_ui()
        self.timeframe_changed.emit(timeframe.value)
        
    def request_analysis(self):
        # We need to ask parent (Overlay) to open input dialog or capture
        # Since logic is coupled, we emit a signal or call parent method if available.
        if self.parent():
             # We assume parent is Overlay
             self.parent().prompt_for_analysis()

    def update_dynamic_actions(self, timeframe):
        # Clear old widgets
        while self.action_layout.count():
            item = self.action_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Add new widgets based on logic
        if timeframe == Timeframe.DAILY:
            # Display detected direction (Read-Only)
            current_dir = self.manager.get_status("daily_direction") or "暂未检测"
            
            info_lbl = QLabel(f"当前日线方向：{current_dir}")
            info_lbl.setStyleSheet("color: #FFD700; font-weight: bold; font-size: 14px; margin-bottom: 5px;")
            info_lbl.setAlignment(Qt.AlignCenter)
            self.action_layout.addWidget(info_lbl)
            
            if current_dir == "暂未检测":
                hint = QLabel("请画线并点击下方‘智能分析’")
                hint.setStyleSheet("color: #AAA; font-size: 11px;")
                hint.setAlignment(Qt.AlignCenter)
                self.action_layout.addWidget(hint)
        
        elif timeframe == Timeframe.H4:
             current_status = self.manager.get_status("h4_status")
             
             btn_adj = QPushButton("标记：调整中")
             btn_adj.setProperty("selected", str(current_status == "调整中").lower())
             btn_adj.style().unpolish(btn_adj); btn_adj.style().polish(btn_adj)
             btn_adj.clicked.connect(lambda: self.set_h4_status("调整中"))
             
             btn_trend = QPushButton("标记：趋势共振")
             btn_trend.setProperty("selected", str(current_status == "趋势共振").lower())
             btn_trend.style().unpolish(btn_trend); btn_trend.style().polish(btn_trend)
             btn_trend.clicked.connect(lambda: self.set_h4_status("趋势共振"))
             
             self.action_layout.addWidget(btn_adj)
             self.action_layout.addWidget(btn_trend)

    # --- Dragging Logic ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self.drag_start_pos:
            self.move(event.globalPosition().toPoint() - self.drag_start_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_pos = None
            event.accept()

    def set_daily_dir(self, direction):
        self.manager.update_status("daily_direction", direction)
        self.refresh_ui()

    def set_h4_status(self, status):
        self.manager.update_status("h4_status", status)
        self.refresh_ui()

    def reset_strategy(self):
        # Reset everything
        self.manager.session_data = {
            "current_timeframe": Timeframe.DAILY.value,
            "checklist": {}
        }
        self.manager.save_session()
        self.refresh_ui()
        self.timeframe_changed.emit(Timeframe.DAILY.value) # Force update overlay

