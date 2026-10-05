from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QScrollArea,
    QStatusBar,
)

from ui.header import Header
from ui.input_card import InputCard
from ui.process_card import ProcessCard
from ui.output_console import OutputConsole


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("InvoiceFlow")
        self.resize(960, 720)

        self.status_bar = QStatusBar()
        self.status_bar.showMessage("Ready • InvoiceFlow v1.0")
        self.setStatusBar(self.status_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)

        self.setCentralWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        self.header = Header()
        self.input_card = InputCard()
        self.process_card = ProcessCard()
        self.output_console = OutputConsole()

        self.process_card.file_selected.connect(
            self.update_input_file
        )

        self.input_card.change_file_requested.connect(
            self.process_card.select_input_file
        )

        self.process_card.log_callback = self.output_console.log

        layout.addWidget(self.header)
        layout.addWidget(self.input_card)
        layout.addWidget(self.process_card)
        layout.addWidget(self.output_console)
        layout.addStretch()

    def update_input_file(self, file_path):
        """Update the selected-file display only.

        Input validation and file reading belong to ProcessCard's
        worker/backend pipeline, so selecting a file never performs
        backend I/O from the main window.
        """
        if not file_path:
            return

        path = Path(file_path)

        if not path.is_file():
            self.input_card.file.setText("📊 No file selected")
            self.status_bar.showMessage("Selected input file is not available.")
            return

        self.input_card.file.setText(f"📊 {path.name}")
        self.status_bar.showMessage(f"Input selected • {path.name}")
