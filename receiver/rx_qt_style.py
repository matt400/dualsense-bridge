"""Receiver: motyw okna (ciemny, akcent w kolorze PlayStation): kolory, QSS ze skalą, paleta Qt.

Skala: tryb TV (pełny ekran) powiększa czcionki, odstępy i stałe szerokości (MainWindow.apply_scale) - okno
czytelne z kanapy. QSS jest generowany dla danej skali (qss(scale)); px(n) przelicza wymiary w kodzie okna.
"""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG, CARD, CARD_HI, BORDER, TRACK, LINE = "#0e1016", "#161922", "#1b1f2a", "#252a36", "#1f2430", "#323849"
FG, DIM, MUTED = "#eceef4", "#8d93a5", "#5a6072"
OK, WARN, BAD, CHARGE, ACCENT, ACCENT_HI = "#3ecf8e", "#ffb547", "#ff6b6b", "#5bc0ff", "#4c8dff", "#6ea2ff"
LEVEL = {"ok": OK, "warn": WARN, "bad": BAD, "charge": CHARGE}     # poziom z rx_status -> kolor
# odcinki opóźnienia (karta pada, rx_latency): Bluetooth, telefon, sieć, PC
SEGMENT = {"bt": "#a78bfa", "phone": "#34d3c9", "net": ACCENT, "pc": OK}
TV_SCALE = 1.35

scale = 1.0
_palette = False      # ciemna paleta ustawiona (raz - zmiana skali podmienia tylko QSS)


def px(n):
    """Wymiar w pikselach w bieżącej skali (tryb TV)."""
    return round(n * scale)


def qss(k=1.0):
    def p(n):
        return f"{round(n * k)}px"
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Noto Sans", sans-serif; font-size: {p(13)}; color: {FG}; outline: none; }}
QWidget {{ background: transparent; }}
#root {{ background: {BG}; }}
QToolTip {{ background: {CARD_HI}; color: {FG}; border: 1px solid {LINE}; border-radius: 6px; padding: {p(6)} {p(8)}; }}

#header {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
#banner {{ background: #2a2210; border-bottom: 1px solid #5c4a12; }}
#title {{ font-size: {p(16)}; font-weight: 700; letter-spacing: 0.3px; }}
#hint, .dim {{ color: {DIM}; }}
QLabel[role="dim"] {{ color: {DIM}; font-size: {p(12)}; }}
QLabel[role="small"] {{ color: {MUTED}; font-size: {p(11)}; }}
QLabel[role="h2"] {{ color: {DIM}; font-size: {p(11)}; font-weight: 700; letter-spacing: 0.8px; }}
QLabel[role="h3"] {{ font-size: {p(17)}; font-weight: 600; }}
QLabel[role="name"] {{ font-size: {p(14)}; font-weight: 700; }}
QLabel[role="val"] {{ font-weight: 600; }}
QLabel[role="big"] {{ font-size: {p(15)}; font-weight: 700; }}
QLabel[level="ok"] {{ color: {OK}; }}
QLabel[level="warn"] {{ color: {WARN}; }}
QLabel[level="bad"] {{ color: {BAD}; }}
QLabel#pill {{ background: {TRACK}; border: 1px solid {BORDER}; border-radius: {p(11)}; padding: {p(3)} {p(10)}; font-size: {p(12)}; }}

QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: {p(14)}; }}
QFrame#card:hover {{ border-color: {LINE}; }}
QFrame#card[selected="true"] {{ border: 1px solid {ACCENT}; background: {CARD_HI}; }}
QFrame#card[low="true"] {{ border: 1px solid {BAD}; }}
QFrame#card[off="true"] {{ background: transparent; border: 1px dashed {BORDER}; }}
QFrame#row {{ background: {TRACK}; border-radius: {p(8)}; }}
QFrame#panel {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: {p(14)}; }}
QFrame#group {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: {p(12)}; }}
QFrame#hsep {{ background: {BORDER}; min-width: 1px; max-width: 1px; }}
QFrame#vsep {{ background: {BORDER}; min-height: 1px; max-height: 1px; }}

QPushButton {{ background: {TRACK}; border: 1px solid {LINE}; border-radius: {p(8)}; padding: {p(6)} {p(14)}; }}
QPushButton:hover:!disabled {{ border-color: {ACCENT}; }}
QPushButton:focus {{ border-color: {ACCENT_HI}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: #ffffff; border-color: {ACCENT}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HI}; }}
QPushButton[primary="true"]:disabled {{ background: {TRACK}; color: {MUTED}; border-color: {BORDER}; }}
QPushButton[armed="true"] {{ background: {BAD}; color: {BG}; border-color: {BAD}; font-weight: 600; }}
QPushButton[small="true"] {{ padding: {p(3)} {p(10)}; font-size: {p(12)}; }}
QPushButton[iconic="true"] {{ background: transparent; border: 1px solid transparent; border-radius: {p(8)}; padding: {p(5)}; }}
QPushButton[iconic="true"]:hover:!disabled {{ background: {TRACK}; border-color: {LINE}; }}
QPushButton[iconic="true"]:focus {{ border-color: {ACCENT_HI}; }}
QPushButton[iconic="true"][armed="true"] {{ background: {BAD}; border-color: {BAD}; padding: {p(5)} {p(10)}; }}
QPushButton#fold {{ background: transparent; border: none; color: {DIM}; font-size: {p(11)}; font-weight: 700;
                    letter-spacing: 0.8px; padding: {p(4)} 0; text-align: left; }}
QPushButton#fold:hover {{ color: {FG}; }}
QPushButton#fold:focus {{ color: {ACCENT_HI}; }}
QFrame#segment {{ background: {TRACK}; border: 1px solid {LINE}; border-radius: {p(10)}; }}
QPushButton[seg="true"] {{ background: transparent; border: none; border-radius: {p(7)}; padding: {p(5)} {p(12)}; color: {DIM}; }}
QPushButton[seg="true"]:hover:!checked {{ color: {FG}; background: #262b38; }}
QPushButton[seg="true"]:checked {{ background: {ACCENT}; color: #ffffff; font-weight: 600; }}
QToolButton {{ background: transparent; border: none; color: {DIM}; font-size: {p(16)}; padding: 0 {p(4)}; }}
QToolButton:hover {{ color: {BAD}; }}
QToolButton#crown {{ padding: 0 {p(2)}; border-radius: {p(6)}; }}
QToolButton#crown:hover {{ background: {TRACK}; }}

QComboBox, QLineEdit, QSpinBox, QPlainTextEdit {{ background: {TRACK}; border: 1px solid {LINE}; border-radius: {p(8)};
                       padding: {p(6)} {p(10)}; selection-background-color: {ACCENT}; selection-color: #ffffff; }}
QComboBox:hover, QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: {p(18)}; border: none; background: transparent; }}
QComboBox::drop-down {{ border: none; width: {p(22)}; }}
QComboBox QAbstractItemView {{ background: {CARD_HI}; border: 1px solid {LINE}; selection-background-color: {ACCENT};
                              selection-color: #ffffff; outline: none; padding: {p(3)}; }}
QCompleter QAbstractItemView, QListView {{ background: {CARD_HI}; border: 1px solid {LINE}; }}

QCheckBox {{ spacing: {p(8)}; }}
QCheckBox::indicator {{ width: {p(16)}; height: {p(16)}; border-radius: {p(4)}; border: 1px solid #4a5062; background: {TRACK}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox:focus {{ color: {ACCENT_HI}; }}

QSlider {{ min-height: {p(18)}; }}
QSlider::groove:horizontal {{ height: {p(4)}; border-radius: {p(2)}; background: {LINE}; }}
QSlider::sub-page:horizontal {{ height: {p(4)}; border-radius: {p(2)}; background: {ACCENT}; }}
QSlider::add-page:horizontal {{ height: {p(4)}; border-radius: {p(2)}; background: {LINE}; }}
QSlider::sub-page:horizontal:disabled {{ background: {MUTED}; }}
QSlider::handle:horizontal {{ width: {p(10)}; height: {p(10)}; margin: -{p(6)} 0; border-radius: {p(8)}; background: #ffffff;
                             border: {p(3)} solid {ACCENT}; }}
QSlider::handle:horizontal:disabled {{ border-color: {MUTED}; background: {LINE}; }}
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus {{ border-color: {ACCENT_HI}; background: {ACCENT_HI}; }}

QTabWidget::pane {{ border: none; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {DIM}; padding: {p(6)} {p(12)}; margin-right: {p(4)}; border-radius: {p(8)};
               border: 1px solid transparent; }}
QTabBar::tab:hover {{ color: {FG}; background: {TRACK}; }}
QTabBar::tab:selected {{ background: {TRACK}; color: {FG}; border-color: {LINE}; font-weight: 600; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: {p(8)}; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: {p(4)}; min-height: {p(24)}; }}
QScrollBar::handle:vertical:hover {{ background: #4a5062; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QMenu {{ background: {CARD_HI}; border: 1px solid {LINE}; padding: {p(4)}; border-radius: {p(8)}; }}
QMenu::item {{ padding: {p(6)} {p(18)}; border-radius: {p(6)}; }}
QMenu::item:selected {{ background: {TRACK}; }}
QMessageBox {{ background: {CARD}; }}
"""


QSS = qss()


def apply_theme(app: QApplication, k=1.0):
    """Styl Fusion + ciemna paleta (dla elementów, których QSS nie obejmuje: okna dialogowe, podpowiedzi) + QSS.
    k = skala (tryb TV); zmiana skali tylko podmienia QSS, paleta zostaje."""
    global scale, _palette
    scale = k
    if not _palette:
        _palette = True
        app.setStyle("Fusion")
        pal = QPalette()
        for role, color in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, CARD),
                            (QPalette.ColorRole.Button, TRACK), (QPalette.ColorRole.WindowText, FG),
                            (QPalette.ColorRole.Text, FG), (QPalette.ColorRole.ButtonText, FG),
                            (QPalette.ColorRole.ToolTipBase, CARD_HI), (QPalette.ColorRole.ToolTipText, FG),
                            (QPalette.ColorRole.Highlight, ACCENT), (QPalette.ColorRole.HighlightedText, "#ffffff"),
                            (QPalette.ColorRole.PlaceholderText, MUTED)):
            pal.setColor(role, QColor(color))
        app.setPalette(pal)
    app.setStyleSheet(qss(k))


def set_prop(widget, name, value):
    """Ustawia właściwość używaną w QSS ([level="ok"], [selected="true"]...) i odświeża styl tylko, gdy się zmieniła."""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
