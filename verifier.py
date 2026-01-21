
import sys
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                               QLabel, QLineEdit, QPushButton, QTableWidget, 
                               QTableWidgetItem, QHeaderView)
from PySide6.QtCore import Qt
from algorithms import BoshenAlgorithms

class BoshenVerifier(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("波神数值验证器 (Boshen Verifier)")
        self.resize(600, 400)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Inputs
        input_layout = QHBoxLayout()
        
        self.start_input = QLineEdit()
        self.start_input.setPlaceholderText("起点价格 (Start Price)")
        input_layout.addWidget(QLabel("起点:"))
        input_layout.addWidget(self.start_input)
        
        self.end_input = QLineEdit()
        self.end_input.setPlaceholderText("终点价格 (End Price)")
        input_layout.addWidget(QLabel("终点:"))
        input_layout.addWidget(self.end_input)
        
        calc_btn = QPushButton("计算 (Calculate)")
        calc_btn.clicked.connect(self.calculate)
        input_layout.addWidget(calc_btn)
        
        layout.addLayout(input_layout)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["线 (Line)", "系数 (Ratio)", "计算值 (Our Value)", "原版值 (Original)", "误差 (Diff)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)
        
        # Validation Button
        validate_btn = QPushButton("对比误差 (Compare Diff)")
        validate_btn.clicked.connect(self.update_diffs)
        layout.addWidget(validate_btn)

        self.setLayout(layout)

    def calculate(self):
        try:
            start = float(self.start_input.text())
            end = float(self.end_input.text())
        except ValueError:
            return

        levels = BoshenAlgorithms.calculate_levels(start, end)
        
        self.table.setRowCount(len(levels))
        for i, (ratio, val) in enumerate(levels):
            # Line Number
            self.table.setItem(i, 0, QTableWidgetItem(f"Line {i+1}"))
            
            # Ratio
            self.table.setItem(i, 1, QTableWidgetItem(str(ratio)))
            
            # Our Value (Read Only)
            val_item = QTableWidgetItem(f"{val:.4f}")
            val_item.setFlags(val_item.flags() ^ Qt.ItemIsEditable)
            self.table.setItem(i, 2, val_item)
            
            # Original Value (Editable)
            if not self.table.item(i, 3):
                self.table.setItem(i, 3, QTableWidgetItem(""))
            
            # Diff
            if not self.table.item(i, 4):
                 self.table.setItem(i, 4, QTableWidgetItem(""))

    def update_diffs(self):
        for i in range(self.table.rowCount()):
            our_val_item = self.table.item(i, 2)
            orig_val_item = self.table.item(i, 3)
            
            if our_val_item and orig_val_item and orig_val_item.text():
                try:
                    our_val = float(our_val_item.text())
                    orig_val = float(orig_val_item.text())
                    diff = our_val - orig_val
                    
                    diff_item = QTableWidgetItem(f"{diff:.4f}")
                    if abs(diff) > 0.01:
                        diff_item.setBackground(Qt.red)
                    else:
                        diff_item.setBackground(Qt.green)
                        
                    self.table.setItem(i, 4, diff_item)
                except ValueError:
                    pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = BoshenVerifier()
    win.show()
    sys.exit(app.exec())
