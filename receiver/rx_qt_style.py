"""Receiver: motyw okna (ciemny): kolory, QSS, paleta Qt."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG, CARD, BORDER, TRACK = "#14161c", "#1a1d25", "#272b36", "#2a2e3a"
FG, DIM, MUTED = "#e8eaf0", "#8b91a1", "#555b6b"
OK, WARN, BAD, CHARGE, ACCENT = "#4cd08a", "#ffb300", "#ff6b6b", "#5b9bff", "#ffb300"
LEVEL = {"ok": OK, "warn": WARN, "bad": BAD, "charge": CHARGE}     # poziom z rx_status -> kolor

QSS = f"""
* {{ font-family: "Segoe UI", "Noto Sans", sans-serif; font-size: 13px; color: {FG}; outline: none; }}
QWidget {{ background: transparent; }}
#root {{ background: {BG}; }}
QToolTip {{ background: {CARD}; color: {FG}; border: 1px solid {BORDER}; padding: 4px 6px; }}

#header {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
#banner {{ background: #2a2410; border-bottom: 1px solid #5c4a12; }}
#title {{ font-size: 16px; font-weight: 600; }}
#hint, .dim {{ color: {DIM}; }}
QLabel[role="dim"] {{ color: {DIM}; font-size: 12px; }}
QLabel[role="h2"] {{ color: {DIM}; font-size: 11px; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 17px; font-weight: 600; }}
QLabel[role="name"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="val"] {{ font-weight: 600; }}
QLabel[level="ok"] {{ color: {OK}; }}
QLabel[level="warn"] {{ color: {WARN}; }}
QLabel[level="bad"] {{ color: {BAD}; }}

QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#card[selected="true"] {{ border: 1px solid {ACCENT}; }}
QFrame#card[low="true"] {{ border: 1px solid {BAD}; }}
QFrame#row {{ background: {TRACK}; border-radius: 8px; }}
QFrame#panel {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#group {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#hsep {{ background: {BORDER}; min-width: 1px; max-width: 1px; }}

QPushButton {{ background: {TRACK}; border: 1px solid #343a4a; border-radius: 8px; padding: 6px 12px; }}
QPushButton:hover:!disabled {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: {BG}; border-color: {ACCENT}; font-weight: 600; }}
QPushButton[primary="true"]:disabled {{ background: {TRACK}; color: {MUTED}; border-color: #343a4a; }}
QPushButton[armed="true"] {{ background: {BAD}; color: {BG}; border-color: {BAD}; font-weight: 600; }}
QPushButton[small="true"] {{ padding: 3px 10px; font-size: 12px; }}
QFrame#segment {{ background: {TRACK}; border: 1px solid #343a4a; border-radius: 10px; }}
QPushButton[seg="true"] {{ background: transparent; border: none; border-radius: 7px; padding: 5px 12px; color: {DIM}; }}
QPushButton[seg="true"]:hover:!checked {{ color: {FG}; background: #2c3140; }}
QPushButton[seg="true"]:checked {{ background: #454c63; color: #ffffff; font-weight: 600; }}
QToolButton {{ background: transparent; border: none; color: {DIM}; font-size: 16px; padding: 0 4px; }}
QToolButton:hover {{ color: {BAD}; }}
QToolButton#crown {{ padding: 0 2px; border-radius: 6px; }}
QToolButton#crown:hover {{ background: {TRACK}; }}

QComboBox, QLineEdit, QSpinBox, QPlainTextEdit {{ background: {TRACK}; border: 1px solid #343a4a; border-radius: 8px; padding: 6px 10px;
                       selection-background-color: {ACCENT}; selection-color: {BG}; }}
QComboBox:hover, QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: none; background: transparent; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
                              selection-color: {BG}; outline: none; padding: 2px; }}
QCompleter QAbstractItemView, QListView {{ background: {CARD}; border: 1px solid {BORDER}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #4a5062; background: {TRACK}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {TRACK}; }}
QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {ACCENT}; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {ACCENT}; }}
QSlider::handle:horizontal:hover {{ background: #ffc233; }}

QTabWidget::pane {{ border: none; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: {TRACK}; color: #c9cddb; padding: 6px 14px; margin-right: 4px; border-radius: 8px; }}
QTabBar::tab:hover {{ color: #ffffff; }}
QTabBar::tab:selected {{ background: {ACCENT}; color: {BG}; font-weight: 600; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #343a4a; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #4a5062; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {TRACK}; }}
QMessageBox {{ background: {CARD}; }}
"""


def apply_theme(app: QApplication):
    """Styl Fusion + ciemna paleta (dla elementów, których QSS nie obejmuje: okna dialogowe, podpowiedzi) + QSS."""
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, CARD), (QPalette.ColorRole.Button, TRACK),
                        (QPalette.ColorRole.WindowText, FG), (QPalette.ColorRole.Text, FG),
                        (QPalette.ColorRole.ButtonText, FG), (QPalette.ColorRole.ToolTipBase, CARD),
                        (QPalette.ColorRole.ToolTipText, FG), (QPalette.ColorRole.Highlight, ACCENT),
                        (QPalette.ColorRole.HighlightedText, BG), (QPalette.ColorRole.PlaceholderText, MUTED)):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def set_prop(widget, name, value):
    """Ustawia właściwość używaną w QSS ([level="ok"], [selected="true"]...) i odświeża styl tylko, gdy się zmieniła."""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
