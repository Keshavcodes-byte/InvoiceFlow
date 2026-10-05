from html import escape

from PySide6.QtWidgets import (
    QLabel,
    QTextEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
)
from PySide6.QtCore import QTime

from ui.hover_card import HoverCard


class OutputConsole(HoverCard):

    def __init__(self):
        super().__init__()

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

        QTextEdit {
            background: #0B1220;
            color: #E2E8F0;
            border: 1px solid #1E293B;
            border-radius: 14px;
            padding: 14px;
            font-family: Consolas;
            font-size: 12px;
        }

        QPushButton {
            background: #2563EB;
            color: white;
            border: none;
            border-radius: 10px;
            padding: 10px 18px;
            font-weight: 600;
        }

        QPushButton:hover {
            background: #1D4ED8;
        }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        # Title
        title = QLabel("📋 Output Console")
        title.setStyleSheet("""
            font-size: 22px;
            font-weight: 700;
            color: #111827;
        """)

        layout.addWidget(title)

        # Live status
        status_row = QHBoxLayout()

        self.live_status = QLabel("● LIVE")
        self.live_status.setObjectName("liveStatus")

        status_row.addWidget(self.live_status)
        status_row.addStretch()

        layout.addLayout(status_row)

        # Console
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(200)
        self.console.setAcceptRichText(True)
        self.console.setUndoRedoEnabled(False)

        layout.addWidget(self.console)

        # Clear button
        bottom = QHBoxLayout()

        self.clear_btn = QPushButton("🗑 Clear Logs")
        self.clear_btn.clicked.connect(
            self.console.clear
        )

        bottom.addStretch()
        bottom.addWidget(self.clear_btn)

        layout.addLayout(bottom)

        # Initial logs
        self.log("InvoiceFlow Started")
        self.log("Waiting for Generate PDFs...")

    def log(self, message, level="info"):

        current = QTime.currentTime().toString("HH:mm:ss")

        if level == "success":
            icon = "✓"
            color = "#4ADE80"

        elif level == "warning":
            icon = "⚠"
            color = "#FACC15"

        elif level == "error":
            icon = "✕"
            color = "#F87171"

        else:
            icon = "•"
            color = "#94A3B8"

        safe_message = escape(str(message))

        html = (
            f'<span style="color:#64748B;">{current}</span> '
            f'<span style="color:{color}; font-weight:700;">'
            f'{icon}</span> '
            f'<span style="color:#E2E8F0;">'
            f'{safe_message}</span>'
        )

        self.console.append(html)

        scrollbar = self.console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())