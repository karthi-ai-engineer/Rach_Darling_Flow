"""Draw the small images the window's stylesheet needs (a checkbox tick, dropdown arrows) into sst/static/ui.

Qt stylesheets can only show these as image files. They are drawn from Windows' own icon font, at 1x and 2x (Qt picks
the @2x file on high-DPI screens). Run it again after changing a colour in sst/window.py's THEMES:

  uv run python scripts/make_ui_images.py
"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")  # the real fonts
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from sst.window import ICON_FONTS, THEMES

OUT = Path(__file__).resolve().parent.parent / "sst" / "static" / "ui"
CHECK, CHEVRON_DOWN = "\ue73e", "\ue70d"


def draw(glyph: str, colour: str, size: int, path: Path) -> None:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = QFont()
    font.setFamilies(ICON_FONTS)
    font.setPixelSize(round(size * 0.75))
    p.setFont(font)
    p.setPen(QColor(colour))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    image.save(str(path))


def main() -> None:
    QApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    for scale, suffix in ((1, ""), (2, "@2x")):
        draw(CHECK, "#ffffff", 16 * scale, OUT / f"check{suffix}.png")
        for theme, colours in THEMES.items():
            draw(CHEVRON_DOWN, colours["muted"], 12 * scale, OUT / f"arrow-{theme}{suffix}.png")
    print("Wrote", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
