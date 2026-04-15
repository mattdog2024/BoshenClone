"""
双屏播放器 DualScreenPlayer v2.0
主入口 - 带崩溃日志保护
"""
import sys
import os
import traceback

# 崩溃日志路径（放在 exe 同目录）
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "crash.log")


def write_log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            import datetime
            f.write(f"[{datetime.datetime.now()}] {msg}\n")
    except Exception:
        pass


def main():
    write_log("程序启动")
    try:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import Qt, QCoreApplication
        write_log("PyQt5 导入成功")

        QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

        app = QApplication(sys.argv)
        app.setApplicationName("双屏播放器")
        app.setApplicationVersion("2.0")

        write_log("QApplication 创建成功")

        from main_window import MainWindow
        write_log("MainWindow 导入成功")

        window = MainWindow()
        window.show()
        write_log("窗口显示成功")

        sys.exit(app.exec_())

    except Exception as e:
        err = traceback.format_exc()
        write_log(f"启动失败:\n{err}")
        # 弹出错误提示
        try:
            from PyQt5.QtWidgets import QApplication, QMessageBox
            if not QApplication.instance():
                _app = QApplication(sys.argv)
            QMessageBox.critical(None, "启动错误",
                                 f"程序启动失败！\n\n错误信息：\n{str(e)}\n\n详细日志：{LOG_PATH}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
