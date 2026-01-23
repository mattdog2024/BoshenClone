
import sys
from PySide6.QtWidgets import QApplication
from ui_toolbar import BoshenToolbar
from overlay import Overlay
from ui_analysis import AnalysisDialog

def main():
    app = QApplication(sys.argv)
    
    # Global Light Theme Stylesheet
    app.setStyleSheet("""
        QWidget {
            background-color: #F0F0F0;
            color: black;
            font-family: "SimSun";
        }
        QMenu {
            background-color: #F0F0F0;
            border: 1px solid #A0A0A0;
        }
        QMenu::item:selected {
            background-color: #90C8F6;
            color: black;
        }
        QLineEdit {
            background-color: white;
            color: black;
            border: 1px solid #A0A0A0;
        }
        QPushButton {
            background-color: #E0E0E0;
            border: 1px solid #A0A0A0;
            padding: 4px;
        }
        /* Fix for Dialogs becoming black/transparent */
        QDialog, QMessageBox, QInputDialog {
            background-color: #FFFFFF;
            color: black;
            border: 1px solid #888;
        }
        QMessageBox QLabel, QInputDialog QLabel {
            color: black;
            background-color: transparent;
        }
    """)
    
    # Create the Overlay (Full screen, transparent)
    overlay = Overlay()
    # Force geometry to primary screen
    screen_geo = app.primaryScreen().geometry()
    overlay.setGeometry(screen_geo)
    overlay.showFullScreen()
    
    # Create the Toolbar (Float on top)
    toolbar = BoshenToolbar()
    toolbar.show()
    
    # Connect signals
    def on_tool_selected(tool_name):
        if tool_name == "量":
            # Open Analysis Dialog with SNAPSHOT DATA
            dialog = AnalysisDialog(overlay.analysis_data, parent=toolbar)
            dialog.exec()
            # Dont set tool to "量", just keep previous or None
        else:
            overlay.set_tool(tool_name)
        
    toolbar.tool_selected.connect(on_tool_selected)
    
    toolbar.clear_requested.connect(overlay.clear_all)
    toolbar.close_requested.connect(app.quit)
    toolbar.color_changed.connect(overlay.set_line_color)
    toolbar.fast_mode_toggled.connect(overlay.toggle_fast_mode)
    toolbar.timeframe_changed.connect(overlay.set_timeframe)
    toolbar.save_requested.connect(overlay.save_snapshot)
    
    # Optional: Position toolbar initially
    toolbar.move(100, 100)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
