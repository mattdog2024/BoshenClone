
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                               QTableWidget, QTableWidgetItem, QPushButton, 
                               QTextEdit, QGroupBox, QHeaderView, QLineEdit)
from PySide6.QtCore import Qt
from algorithms import BoshenAlgorithms

class AnalysisDialog(QDialog):
    def __init__(self, analysis_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("波神凯线多周期共振分析 (Multi-Timeframe Analysis)")
        self.resize(1000, 600)
        self.analysis_data = analysis_data # Expects dict: {'日线': [], '4小时': [], '1小时': []}
        
        self.init_ui()
        self.load_data()
        
    def init_ui(self):
        main_layout = QVBoxLayout()
        
        # Top: Control Area
        top_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新数据 (Refresh)")
        self.refresh_btn.clicked.connect(self.load_data)
        top_layout.addWidget(self.refresh_btn)
        top_layout.addStretch()
        main_layout.addLayout(top_layout)
        
        # Middle: 3 Columns for Timeframes
        data_layout = QHBoxLayout()
        
        self.table_daily = self.create_timeframe_table("日线 (Daily)")
        self.table_4h = self.create_timeframe_table("4小时 (4H)")
        self.table_1h = self.create_timeframe_table("1小时 (1H)")
        
        data_layout.addWidget(self.wrap_in_group("日线数据", self.table_daily))
        data_layout.addWidget(self.wrap_in_group("4小时数据", self.table_4h))
        data_layout.addWidget(self.wrap_in_group("1小时数据", self.table_1h))
        
        main_layout.addLayout(data_layout)
        
        # Bottom: Analysis Results
        result_group = QGroupBox("共振分析结果 (Resonance Results)")
        result_layout = QVBoxLayout()
        
        # Control Row for Analysis
        analysis_ctrl_layout = QHBoxLayout()
        
        self.compare_btn = QPushButton("开始对比分析 (Analyze Overlaps)")
        self.compare_btn.setStyleSheet("background-color: #90C8F6; font-weight: bold; padding: 5px;")
        self.compare_btn.clicked.connect(self.perform_analysis)
        analysis_ctrl_layout.addWidget(self.compare_btn)
        
        analysis_ctrl_layout.addStretch()
        
        analysis_ctrl_layout.addWidget(QLabel("容差 (Diff Limit):"))
        self.tolerance_input = QLineEdit("3.0") # Default to tighter tolerance
        self.tolerance_input.setFixedWidth(50)
        analysis_ctrl_layout.addWidget(self.tolerance_input)
        
        result_layout.addLayout(analysis_ctrl_layout)
        
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        result_layout.addWidget(self.result_text)
        
        result_group.setLayout(result_layout)
        main_layout.addWidget(result_group)
        
        self.setLayout(main_layout)
        
    def create_timeframe_table(self, title):
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["系数 (Ratio)", "价格 (Price)"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return table
        
    def wrap_in_group(self, title, widget):
        group = QGroupBox(title)
        layout = QVBoxLayout()
        layout.addWidget(widget)
        group.setLayout(layout)
        return group
        
    def load_data(self):
        # Clear tables
        self.table_daily.setRowCount(0)
        self.table_4h.setRowCount(0)
        self.table_1h.setRowCount(0)
        
        self.daily_levels = []
        self.four_h_levels = []
        self.one_h_levels = []

        # Helper to process a list for a specifics timeframe
        def process_list(drawing_list, target_table, target_level_list):
            for drawing in drawing_list:
                if 'price_a' not in drawing or 'price_b' not in drawing:
                    continue
                
                # Calculate levels
                levels = BoshenAlgorithms.calculate_levels(drawing['price_a'], drawing['price_b'])
                
                # Add Header
                row = target_table.rowCount()
                target_table.insertRow(row)
                target_table.setItem(row, 0, QTableWidgetItem(f"--- A={drawing['price_a']:.2f} ---"))
                
                for r, price in levels:
                    row = target_table.rowCount()
                    target_table.insertRow(row)
                    target_table.setItem(row, 0, QTableWidgetItem(f"{r}"))
                    target_table.setItem(row, 1, QTableWidgetItem(f"{price:.2f}"))
                    
                    target_level_list.append({
                        'price': price,
                        'ratio': r,
                        'source': f"A={drawing['price_a']:.2f}"
                    })

        # Process from self.analysis_data dictionary
        if '日线' in self.analysis_data:
            process_list(self.analysis_data['日线'], self.table_daily, self.daily_levels)
            
        if '4小时' in self.analysis_data:
            process_list(self.analysis_data['4小时'], self.table_4h, self.four_h_levels)
            
        if '1小时' in self.analysis_data:
            process_list(self.analysis_data['1小时'], self.table_1h, self.one_h_levels)

                    
    def perform_analysis(self):
        results = []
        
        try:
             tolerance = float(self.tolerance_input.text())
        except ValueError:
             tolerance = 3.0 # Fallback
             self.tolerance_input.setText("3.0")
        
        # Helper to find line number from ratio
        
        # Helper to find line number from ratio
        # Ratios: [1.784, 2.351, 3.027, 3.459, 3.865, 4.622, 5.135, 5.865, 6.676]
        # We can just look it up or pass it in.
        ratios = [1.784, 2.351, 3.027, 3.459, 3.865, 4.622, 5.135, 5.865, 6.676]
        
        # User requested Key Lines only: 3, 5, 6, 7, 8
        # Indices: 2, 4, 5, 6, 7
        KEY_LINE_INDICES = [2, 4, 5, 6, 7] 

        def get_line_index(r):
            try:
                for i, std_r in enumerate(ratios):
                    if abs(r - std_r) < 0.001:
                        return i
                return -1
            except:
                return -1

        def get_line_num(r):
            idx = get_line_index(r)
            return idx + 1 if idx != -1 else "?"

        def is_key_line(r):
            return get_line_index(r) in KEY_LINE_INDICES

        def format_resonance(t1_name, l1, t2_name, l2):
            # l1 and l2 are dicts: {'price', 'ratio', ...}
            # Simplified output: "日线1线 和 4小时2线 重合"
            
            line1_num = get_line_num(l1['ratio'])
            line2_num = get_line_num(l2['ratio'])
            
            # Use '和' as requested, remove ratios from main text.
            return (f"{t1_name}{line1_num}线 和 {t2_name}{line2_num}线 重合 "
                    f"(价格: {l1['price']:.2f} ≈ {l2['price']:.2f}, 差: {abs(l1['price'] - l2['price']):.2f})")

        # Compare Daily vs 4H
        for d in self.daily_levels:
            if not is_key_line(d['ratio']): continue # Filter
            
            for f in self.four_h_levels:
                if not is_key_line(f['ratio']): continue # Filter
                
                if abs(d['price'] - f['price']) <= tolerance:
                    results.append(format_resonance("日线", d, "4小时", f))
                    results.append("-" * 40) # Separator
                    
        # Compare Daily vs 1H
        for d in self.daily_levels:
            if not is_key_line(d['ratio']): continue
            
            for o in self.one_h_levels:
                if not is_key_line(o['ratio']): continue
                
                if abs(d['price'] - o['price']) <= tolerance:
                    results.append(format_resonance("日线", d, "1小时", o))
                    results.append("-" * 40)

        # Compare 4H vs 1H
        for f in self.four_h_levels:
            if not is_key_line(f['ratio']): continue
            
            for o in self.one_h_levels:
                if not is_key_line(o['ratio']): continue
                
                if abs(f['price'] - o['price']) <= tolerance:
                    results.append(format_resonance("4小时", f, "1小时", o))
                    results.append("-" * 40)

        if not results:
            self.result_text.setText("未发现明显的共振线位 (No overlaps found within tolerance).")
        else:
            self.result_text.setText("\n".join(results))
