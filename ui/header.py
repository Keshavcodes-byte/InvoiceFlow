from pathlib import Path

from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QGraphicsDropShadowEffect,
)
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtCore import Qt


class Header(QWidget):

    def __init__(self):
        super().__init__()

        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(40)
        self.shadow.setOffset(0, 10)
        self.shadow.setColor(QColor(29, 78, 216, 90))
        self.setGraphicsEffect(self.shadow)

        self.setFixedHeight(120)
        self.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground,
            True,
        )

        self.setStyleSheet("""
        QWidget {
            background: qlineargradient(
                x1: 0,
                y1: 0,
                x2: 1,
                y2: 1,
                stop: 0 #1D4ED8,
                stop: 0.55 #2563EB,
                stop: 1 #3B82F6
            );

            border: 1px solid rgba(255,255,255,35);
            border-radius: 24px;
        }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(16)
        top_row.setAlignment(Qt.AlignLeft)

        logo = QLabel()
        logo.setFixedSize(42, 42)
        logo.setAlignment(Qt.AlignCenter)

        logo_path = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "Icons"
            / "logo.svg"
        )

        pix = QPixmap(str(logo_path))

        if not pix.isNull():
            logo.setPixmap(
                pix.scaled(
                    42,
                    42,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        else:
            # Keep a visible, stable placeholder if the optional logo
            # asset is missing or cannot be loaded.
            logo.setText("IF")
            logo.setStyleSheet("""
                color: white;
                font-size: 14px;
                font-weight: 800;
                border: none;
                background: transparent;
            """)

        title = QLabel("InvoiceFlow")
        title.setStyleSheet("""
            font-size: 36px;
            font-weight: 800;
            color: white;
            border: none;
            background: transparent;
            letter-spacing: 1px;
        """)

        subtitle = QLabel(
            "Bulk PDF Generation Utility • Workflow Dashboard"
        )
        subtitle.setStyleSheet("""
            font-size: 14px;
            color: rgba(255,255,255,220);
            border: none;
            background: transparent;
        """)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.addWidget(title)
        text_layout.addWidget(subtitle)

        top_row.addWidget(logo)
        top_row.addLayout(text_layout)

        layout.addLayout(top_row)
