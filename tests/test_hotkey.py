import pytest

from sst.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, parse_hotkey


@pytest.mark.parametrize("text, expected", [
    ("ctrl+alt+d", (MOD_CONTROL | MOD_ALT, ord("D"))),
    ("Ctrl + Shift + Space", (MOD_CONTROL | MOD_SHIFT, 0x20)),
    ("control+win+7", (MOD_CONTROL | MOD_WIN, ord("7"))),
    ("f9", (0, 0x78)),
    ("alt+f24", (MOD_ALT, 0x87)),
    ("ctrl+ctrl+x", (MOD_CONTROL, ord("X"))),
])
def test_parse_hotkey(text, expected):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize("text", ["ctrl+hyper+x", "ctrl+alt+", "ctrl+alt+esc", ""])
def test_parse_hotkey_rejects_unknown_keys(text):
    with pytest.raises(ValueError, match="unknown key"):
        parse_hotkey(text)
