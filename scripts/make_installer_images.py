"""Draw the installer's Rflow-branded wizard images into packaging/images (BMP, as Inno Setup wants them).

The large image is on the setup's first and last pages, the small one at the top right of the others. Each comes in the
sizes Inno Setup picks from for 100% to 200% display scaling. Run it again after changing the logo or the colours:

  uv run python scripts/make_installer_images.py
"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")  # the real fonts
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "images"
ICON = ROOT / "sst" / "static" / "sst.ico"
ACCENT, ACCENT_2 = QColor("#2563eb"), QColor("#4f46e5")  # the website's and the app's blue and indigo
LARGE = [(164, 314), (205, 393), (246, 471), (328, 628)]  # 100%, 125%, 150%, 200%
SMALL = [55, 69, 83, 110]


def _gradient(image: QImage) -> QLinearGradient:
    gradient = QLinearGradient(QPointF(0, 0), QPointF(image.width(), image.height()))
    gradient.setColorAt(0, ACCENT)
    gradient.setColorAt(1, ACCENT_2)
    return gradient


def large(width: int, height: int, path: Path) -> None:
    scale = width / 164
    image = QImage(width, height, QImage.Format.Format_RGB32)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.fillRect(image.rect(), _gradient(image))
    p.setPen(Qt.PenStyle.NoPen)  # two soft circles, like sound spreading out
    p.setBrush(QColor(255, 255, 255, 22))
    p.drawEllipse(QPointF(width * 0.95, height * 0.18), 90 * scale, 90 * scale)
    p.drawEllipse(QPointF(width * 0.05, height * 0.92), 70 * scale, 70 * scale)
    logo = int(64 * scale)
    tile = logo + 16 * scale  # a white tile, so the blue logo stands out from the blue background
    p.setBrush(QColor("white"))
    p.drawRoundedRect(QRectF((width - tile) / 2, 92 * scale - 8 * scale, tile, tile), 18 * scale, 18 * scale)
    p.drawPixmap(int((width - logo) / 2), int(92 * scale), QIcon(str(ICON)).pixmap(256, 256).scaled(
        logo, logo, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    p.setPen(QColor("white"))
    font = QFont("Segoe UI", 1)
    font.setPixelSize(int(26 * scale))
    font.setWeight(QFont.Weight.Bold)
    p.setFont(font)
    p.drawText(QRectF(0, 168 * scale, width, 36 * scale), Qt.AlignmentFlag.AlignCenter, "Rflow")
    font.setPixelSize(int(11 * scale))
    font.setWeight(QFont.Weight.Normal)
    p.setFont(font)
    p.setPen(QColor(255, 255, 255, 220))
    p.drawText(QRectF(10 * scale, 204 * scale, width - 20 * scale, 40 * scale),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
               "Speak anywhere,\nRflow types it.")
    p.end()
    image.save(str(path))


def small(size: int, path: Path) -> None:
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))  # the wizard's header is white
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    logo = int(size * 0.86)
    p.drawPixmap((size - logo) // 2, (size - logo) // 2, QIcon(str(ICON)).pixmap(256, 256).scaled(
        logo, logo, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    p.end()
    image.save(str(path))


def main() -> None:
    QApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    for width, height in LARGE:
        large(width, height, OUT / f"wizard-{width}.bmp")
    for size in SMALL:
        small(size, OUT / f"wizard-small-{size}.bmp")
    print("Wrote", ", ".join(sorted(p.name for p in OUT.glob("*.bmp"))))


if __name__ == "__main__":
    main()
