"""Typing a dictation (sst.paste), with the clipboard and the keys faked: nothing is pasted for real."""
from contextlib import contextmanager

from sst import paste


def test_line_breaks_reach_the_app_as_windows_line_breaks(monkeypatch):
    # A snippet's lines: a classic edit box shows a bare "\n" as nothing, so the clipboard gets "\r\n".
    puts = []

    @contextmanager
    def session():
        yield

    class User32:
        @staticmethod
        def GetClipboardSequenceNumber():
            return 1

    for name, fake in {"_clipboard": session, "_snapshot": lambda: [], "_put": puts.append, "_press_ctrl_v": lambda: None,
                       "_wait_for_modifiers_released": lambda: None, "user32": User32}.items():
        monkeypatch.setattr(paste, name, fake)
    monkeypatch.setattr(paste.time, "sleep", lambda seconds: None)
    paste.paste_text("Best regards,\nKarthi\r\nKarthi Labs ")
    text = puts[0][0][1].decode("utf-16-le").rstrip("\0")
    assert text == "Best regards,\r\nKarthi\r\nKarthi Labs "
