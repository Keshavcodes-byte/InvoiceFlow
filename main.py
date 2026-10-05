import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from ui.main_window import MainWindow


BASE_DIR = Path(__file__).resolve().parent
STYLE_PATH = BASE_DIR / "styles.qss"


def load_stylesheet(app):
    """Load the application stylesheet without making startup fragile."""
    if not STYLE_PATH.is_file():
        return

    try:
        stylesheet = STYLE_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return

    app.setStyleSheet(stylesheet)


def apply_windows_style(window):
    """Apply optional Windows styling without breaking the application."""
    try:
        import pywinstyles
    except ImportError:
        return

    try:
        pywinstyles.apply_style(window, "mica")
        pywinstyles.change_header_color(window, "#2563EB")
        pywinstyles.change_title_color(window, "white")
    except Exception:
        # Windows visual styling is optional.
        return


def show_startup_error(message):
    """Show a controlled startup error when a GUI is available."""
    try:
        QMessageBox.critical(
            None,
            "InvoiceFlow - Startup Error",
            message,
        )
    except Exception:
        pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("InvoiceFlow")
    app.setApplicationDisplayName("InvoiceFlow")

    load_stylesheet(app)

    try:
        window = MainWindow()
    except Exception as error:
        show_startup_error(
            "InvoiceFlow could not start.\n\n"
            f"Error: {error}"
        )
        return 1

    apply_windows_style(window)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
