from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect
from PySide6.QtCore import QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor


class HoverCard(QFrame):
    def __init__(self):
        super().__init__()

        self.setObjectName("hoverCard")

        # Default Shadow
        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(18)
        self.shadow.setOffset(0, 4)
        self.shadow.setColor(QColor(0, 0, 0, 30))
        self.setGraphicsEffect(self.shadow)

        # Animation
        self.anim = QPropertyAnimation(self.shadow, b"blurRadius")
        self.anim.setDuration(180)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

    def enterEvent(self, event):
        self.anim.stop()
        self.anim.setStartValue(self.shadow.blurRadius())
        self.anim.setEndValue(35)
        self.anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.anim.stop()
        self.anim.setStartValue(self.shadow.blurRadius())
        self.anim.setEndValue(18)
        self.anim.start()
        super().leaveEvent(event)