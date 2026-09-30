"""Render the website's screenshots (site/img) from the real Rflow window, with generic example data.

The window is drawn off the screen (nothing appears, nothing takes focus), light theme, at the screen's scaling:

  uv run python scripts/make_site_screenshots.py
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")  # the real fonts
from PySide6.QtWidgets import QApplication

from sst import window as w
from sst.gateway import GatewayConfig
from sst.settings import Settings, Stats

OUT = Path(__file__).resolve().parent.parent / "site" / "img"
EXAMPLES = [  # (minutes ago, text)
    (12, "Can you review the pull request before lunch and leave a comment if anything looks wrong?"),
    (48, "Thanks for the quick reply! I'll send you the updated draft this afternoon."),
    (60 * 24 + 5, "Let's move the stand-up to Thursday morning and share the notes in Slack."),
    (60 * 24 + 90, "Please book a room for six people on Friday at 2 o'clock."),
]


def preview() -> w.PreviewApp:
    now = datetime.now()
    history = [{"time": (now - timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S"), "text": t} for m, t in EXAMPLES]
    stats = Stats()
    for n, words in enumerate([412, 386, 520, 264, 598, 331, 190, 0, 455, 380, 610]):  # the last three weeks
        if words:
            stats.add(" ".join(["word"] * words), words / 2.3, date.today() - timedelta(days=n))
    stats.add(" ".join(["word"] * 9800), 9800 / 2.3, date.today() - timedelta(days=30))  # the month before
    settings = Settings(welcomed=True, cleanup=True, cleanup_model="gpt-4o-mini", cleanup_fallback="gpt-4.1-nano",
                        vocabulary=["Rflow", "GitHub", "Kubernetes", "Priya", "Q3 roadmap"])
    return w.PreviewApp(settings=settings, history=history, stats=stats,
                        microphones=["Microphone (Realtek(R) Audio)", "Headset (Bluetooth)"],
                        gateway=GatewayConfig("https://api.openai.com/v1", "sk-example-key"))


def shot(app: w.PreviewApp, page: str, name: str, size=(1000, 660)) -> None:
    window = w.MainWindow(app)
    window.setStyleSheet(w.stylesheet("light"))
    window.set_status("Ready: hold Ctrl+Win · cleanup: gpt-4o-mini", True)
    window.resize(*size)
    window.show_page(page)
    QApplication.processEvents()
    window.grab().save(str(OUT / name))


def main() -> None:
    QApplication([])
    app = preview()
    shot(app, "home", "app.png")
    shot(app, "cleanup", "settings.png", size=(1000, 560))
    app.settings.welcomed = False
    shot(app, "welcome", "welcome.png", size=(1000, 760))
    print("Wrote", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
