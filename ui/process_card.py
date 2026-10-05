from pathlib import Path

from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QFileDialog,
)
from PySide6.QtCore import (
    Qt,
    QTimer,
    Signal,
    QObject,
    QThread,
    QUrl,
)
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QDesktopServices,
)

from ui.hover_card import HoverCard
from backend.invoice_generator import generate_pdfs, read_input_file


# ---------------- Circular Progress ---------------- #

class CircularProgress(QWidget):

    def __init__(self):
        super().__init__()

        self.value = 0
        self.setFixedSize(120, 120)

        self.glow_alpha = 80
        self.glow_direction = 1

        self.glow_timer = QTimer(self)
        self.glow_timer.timeout.connect(self.animate_glow)

    def setValue(self, value):
        self.value = max(0, min(100, int(value)))
        self.update()

    def start_glow(self):
        if not self.glow_timer.isActive():
            self.glow_timer.start(35)

    def stop_glow(self):
        if self.glow_timer.isActive():
            self.glow_timer.stop()

    def animate_glow(self):
        self.glow_alpha += self.glow_direction * 3

        if self.glow_alpha >= 130:
            self.glow_alpha = 130
            self.glow_direction = -1

        if self.glow_alpha <= 45:
            self.glow_alpha = 45
            self.glow_direction = 1

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background ring
        pen = QPen(QColor("#DCE3EF"), 10)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(10, 10, 100, 100)

        # Glow layers
        for width, alpha in [
            (22, self.glow_alpha // 4),
            (18, self.glow_alpha // 2),
            (14, self.glow_alpha),
        ]:
            glow_pen = QPen(
                QColor(37, 99, 235, alpha),
                width,
            )
            glow_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(glow_pen)
            painter.drawArc(
                10,
                10,
                100,
                100,
                90 * 16,
                -self.value * 360 / 100 * 16,
            )

        # Main progress ring
        pen = QPen(QColor("#3B82F6"), 10)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(
            10,
            10,
            100,
            100,
            90 * 16,
            -self.value * 360 / 100 * 16,
        )

        # Percentage
        painter.setPen(QColor("#111827"))
        painter.drawText(
            self.rect(),
            Qt.AlignCenter,
            f"{self.value}%",
        )


# ---------------- Invoice Worker ---------------- #

class InvoiceWorker(QObject):

    progress = Signal(int, int, int)
    log = Signal(str, str)
    finished = Signal(int)
    error = Signal(str)

    def __init__(self, file_path, output_folder):
        super().__init__()
        self.file_path = file_path
        self.output_folder = output_folder

    def run(self):
        try:
            self.log.emit("Generating PDFs...", "info")

            df = read_input_file(self.file_path)

            self.log.emit(
                f"Loaded {len(df)} records",
                "success",
            )

            total = generate_pdfs(
                df,
                self.output_folder,
                progress_callback=self.progress.emit,
            )

            self.finished.emit(total)

        except Exception as error:
            self.error.emit(str(error))


# ---------------- Process Card ---------------- #

class ProcessCard(HoverCard):

    file_selected = Signal(str)

    def __init__(self):
        super().__init__()

        self.selected_file = None
        self.log_callback = None
        self.worker_thread = None
        self.worker = None
        self.output_folder = None
        self._close_requested = False

        self.setStyleSheet("""
        QFrame {
            background: white;
            border: 1px solid #E5E7EB;
            border-radius: 20px;
        }

        QLabel {
            border: none;
            background: transparent;
        }

        QPushButton {
            border: none;
            border-radius: 14px;
            padding: 12px;
            font-weight: 700;
            font-size: 14px;
        }

        QPushButton:hover {
            background: #22C55E;
            border: 2px solid rgba(255,255,255,80);
        }

        QPushButton:pressed {
            background: #15803D;
            padding-top: 3px;
        }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(20)

        title = QLabel("⚙ Process")
        title.setStyleSheet("""
            font-size: 22px;
            font-weight: 700;
            color: #111827;
        """)
        layout.addWidget(title)

        row = QHBoxLayout()

        self.input_btn = QPushButton("📂 Select Input")
        self.input_btn.setObjectName("primaryBtn")
        self.input_btn.setCursor(Qt.PointingHandCursor)
        self.input_btn.setToolTip("Select Excel or CSV file")
        self.input_btn.clicked.connect(self.select_input_file)

        self.output_btn = QPushButton("📁 Output")
        self.output_btn.setObjectName("primaryBtn")
        self.output_btn.setCursor(Qt.PointingHandCursor)
        self.output_btn.setToolTip("Open generated Output folder")
        self.output_btn.clicked.connect(self.open_output_folder)
        self.output_btn.setEnabled(False)

        row.addWidget(self.input_btn)
        row.addWidget(self.output_btn)
        layout.addLayout(row)

        self.generate = QPushButton("⚡ Generate PDFs")
        self.generate.setFixedHeight(64)
        self.generate.setObjectName("successBtn")
        self.generate.setCursor(Qt.PointingHandCursor)
        self.generate.setToolTip(
            "Generate PDFs from the selected Excel or CSV file"
        )
        self.generate.clicked.connect(self.generate_invoices)
        layout.addWidget(self.generate)

        progress_row = QHBoxLayout()

        self.circle = CircularProgress()

        right = QVBoxLayout()

        self.status = QLabel("Ready")
        self.status.setStyleSheet("""
            font-size: 18px;
            font-weight: 700;
        """)

        self.info = QLabel("0 / 0 Generated")
        self.info.setStyleSheet("""
            color: #64748B;
            font-size: 13px;
        """)

        right.addWidget(self.status)
        right.addWidget(self.info)
        right.addStretch()

        progress_row.addWidget(self.circle)
        progress_row.addLayout(right)
        layout.addLayout(progress_row)

    def _log(self, message, level="info"):
        if self.log_callback:
            self.log_callback(message, level)

    def select_input_file(self):
        if self.worker_thread is not None:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Input File",
            "",
            "Excel Files (*.xlsx *.xls);;CSV Files (*.csv)",
        )

        if not file_path:
            return

        selected_path = Path(file_path)

        if not selected_path.is_file():
            self._log(
                "The selected input file is no longer available.",
                "error",
            )
            return

        self.selected_file = str(selected_path)
        self.output_folder = None
        self.output_btn.setEnabled(False)
        self.circle.setValue(0)
        self.status.setText("Ready")
        self.info.setText("0 / 0 Generated")

        self.file_selected.emit(self.selected_file)

    def open_output_folder(self):
        if not self.output_folder:
            self._log(
                "Generate PDFs first to create the Output folder.",
                "warning",
            )
            return

        output_path = Path(self.output_folder)

        if not output_path.is_dir():
            self._log(
                "The Output path is not a valid folder.",
                "warning",
            )
            self.output_btn.setEnabled(False)
            return

        try:
            opened = QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(output_path.resolve()))
            )
        except Exception as error:
            self._log(
                f"Could not open the Output folder: {error}",
                "error",
            )
            return

        if not opened:
            self._log(
                "Windows could not open the Output folder.",
                "error",
            )
            return

        self._log(
            f"Opened Output folder: {output_path}",
            "info",
        )

    def generate_invoices(self):
        if not self.selected_file:
            self._log(
                "Please select an input file first.",
                "warning",
            )
            return

        if self.worker_thread is not None:
            return

        input_path = Path(self.selected_file)

        if not input_path.is_file():
            self._log(
                "The selected input file is no longer available.",
                "error",
            )
            self.selected_file = None
            self.output_folder = None
            self.output_btn.setEnabled(False)
            return

        output_folder = input_path.parent / "Output"

        self.output_folder = str(output_folder)
        self.output_btn.setEnabled(False)

        self.circle.setValue(0)
        self.circle.start_glow()
        self.status.setText("Starting...")
        self.info.setText("0 / 0 Generated")

        self.generate.setEnabled(False)
        self.input_btn.setEnabled(False)
        self._close_requested = False

        self.worker_thread = QThread()
        self.worker = InvoiceWorker(
            str(input_path),
            str(output_folder),
        )
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)

        self.worker.progress.connect(self.update_progress)

        self.worker.log.connect(self._forward_worker_log)

        self.worker.finished.connect(self.on_generation_finished)
        self.worker.error.connect(self.on_generation_error)

        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.error.connect(self.worker_thread.quit)

        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(self.worker.deleteLater)

        self.worker_thread.finished.connect(self.on_thread_finished)

        self.worker_thread.start()

    def _forward_worker_log(self, message, level):
        self._log(message, level)

    def update_progress(self, progress, current, total):
        progress = max(0, min(100, int(progress)))
        self.circle.setValue(progress)

        self.status.setText(
            f"Processing • {current}/{total} • {progress}%"
        )
        self.info.setText(
            f"{current} / {total} Generated"
        )

    def on_generation_finished(self, total):
        output_path = Path(self.output_folder) if self.output_folder else None

        if not output_path or not output_path.is_dir():
            self.on_generation_error(
                "PDF generation completed, but the Output folder could not be verified."
            )
            return

        self.circle.setValue(100)
        self.status.setText(
            f"Completed • {total}/{total} • 100%"
        )
        self.info.setText(
            f"{total} / {total} Generated"
        )

        self.output_btn.setEnabled(True)

        self._log(
            f"Generated {total} PDFs successfully!",
            "success",
        )
        self._log(
            f"Output folder: {output_path}",
            "info",
        )

    def on_generation_error(self, error):
        self.circle.setValue(0)
        self.status.setText("Generation failed")
        self.info.setText("0 / 0 Generated")
        self.output_btn.setEnabled(False)

        self._log(
            f"Error: {error}",
            "error",
        )

    def on_thread_finished(self):
        self.circle.stop_glow()

        self.worker_thread = None
        self.worker = None

        if self._close_requested:
            self._close_requested = False
            QTimer.singleShot(0, self.close)
            return

        self.generate.setEnabled(True)
        self.input_btn.setEnabled(True)

    def closeEvent(self, event):
        if self.worker_thread is not None and self.worker_thread.isRunning():
            self._close_requested = True

            self.generate.setEnabled(False)
            self.input_btn.setEnabled(False)
            self.output_btn.setEnabled(False)
            self.status.setText("Finishing current generation...")

            self._log(
                "Generation is still running. Closing after it finishes.",
                "warning",
            )

            event.ignore()
            return

        self.circle.stop_glow()
        event.accept()
