from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
)
from PySide6.QtCore import Signal

from ui.hover_card import HoverCard


class InputCard(HoverCard):

    change_file_requested = Signal()

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

        QPushButton {
            background: #2563EB;
            color: white;
            border: none;
            border-radius: 14px;
            padding: 12px 22px;
            font-weight: 700;
        }

        QPushButton:hover {
            background: #1D4ED8;
        }

        QPushButton:pressed {
            background: #1E40AF;
        }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)

        # Title
        title = QLabel("📥 Input File")
        title.setStyleSheet("""
            font-size: 18px;
            font-weight: 700;
            color: #111827;
        """)

        layout.addWidget(title)

        # File display box
        box = HoverCard()

        box.setStyleSheet("""
        QFrame {
            background: #F8FAFC;
            border: 1px solid #E5E7EB;
            border-radius: 16px;
        }
        """)

        inner = QHBoxLayout(box)
        inner.setContentsMargins(18, 16, 18, 16)

        left = QVBoxLayout()

        self.file = QLabel("📊 No file selected")
        self.file.setStyleSheet("""
            font-size: 16px;
            font-weight: 700;
        """)

        left.addWidget(self.file)

        inner.addLayout(left)
        inner.addStretch()

        # Change file button
        self.change_button = QPushButton("Change File")
        self.change_button.clicked.connect(
            self.change_file_requested.emit
        )

        inner.addWidget(self.change_button)

        layout.addWidget(box)