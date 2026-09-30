import ctypes
import struct

import pytest

from sst.hotkey import INPUT, VK_ESCAPE, Matcher, parse_hotkey

LCTRL, RCTRL, LWIN, RWIN, LALT, LSHIFT, MENU, D, LEFT, SPACE = 0xA2, 0xA3, 0x5B, 0x5C, 0xA4, 0xA0, 0x5D, 0x44, 0x25, 0x20


def feed(matcher, *steps):
    """steps like ('down', LCTRL); returns the list of (hidden, event) results."""
    return [matcher.feed(vk, action == "down") for action, vk in steps]


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("text, modifiers, key", [
    ("ctrl+win", {"ctrl", "win"}, None),
    ("Win + Ctrl", {"ctrl", "win"}, None),
    ("menu", set(), 0x5D),
    ("ctrl+alt+d", {"ctrl", "alt"}, ord("D")),
    ("control+shift+space", {"ctrl", "shift"}, 0x20),
    ("f9", set(), 0x78),
    ("muhenkan", set(), 0x1D),
])
def test_parse_hotkey(text, modifiers, key):
    hotkey = parse_hotkey(text)
    assert hotkey.modifiers == modifiers and hotkey.key == key


@pytest.mark.parametrize("text", ["ctrl+hyper+x", "ctrl+alt+", "d+ctrl", "", "esc"])
def test_parse_hotkey_rejects_unknown_keys(text):
    with pytest.raises(ValueError, match="unknown key"):
        parse_hotkey(text)


@pytest.mark.parametrize("text", ["ctrl", "win"])
def test_a_single_modifier_is_refused(text):
    with pytest.raises(ValueError, match="normal typing"):
        parse_hotkey(text)


def test_labels():
    assert parse_hotkey("ctrl+win").label == "Ctrl+Win"
    assert parse_hotkey("menu").label == "Menu key"


def test_input_struct_has_the_size_sendinput_expects():
    # SendInput silently does nothing when cbSize is wrong: 40 bytes on 64-bit Windows, 28 on 32-bit.
    assert ctypes.sizeof(INPUT) == (40 if struct.calcsize("P") == 8 else 28)


# ---------------------------------------------------------------- Ctrl+Win (modifiers only)

def test_ctrl_win_press_and_release_pass_through():
    m = Matcher(parse_hotkey("ctrl+win"))
    assert feed(m, ("down", LCTRL), ("down", LWIN), ("up", LWIN), ("up", LCTRL)) == [
        (False, None), (False, "press"), (False, "release"), (False, None)]


def test_ctrl_win_in_either_order_and_either_side():
    m = Matcher(parse_hotkey("ctrl+win"))
    assert feed(m, ("down", RWIN), ("down", RCTRL))[-1] == (False, "press")
    assert feed(m, ("up", RCTRL))[-1] == (False, "release")


def test_auto_repeat_while_holding_does_not_press_again():
    m = Matcher(parse_hotkey("ctrl+win"))
    events = [event for _, event in feed(m, ("down", LCTRL), ("down", LWIN), ("down", LWIN), ("down", LCTRL),
                                          ("down", LWIN), ("up", LWIN))]
    assert events == [None, "press", None, None, None, "release"]


def test_windows_shortcuts_still_work_and_interrupt_dictation():
    m = Matcher(parse_hotkey("ctrl+win"))
    results = feed(m, ("down", LCTRL), ("down", LWIN), ("down", D), ("up", D), ("up", LWIN), ("up", LCTRL))
    assert results == [(False, None), (False, "press"), (False, "interrupt"), (False, None), (False, None),
                       (False, None)]  # D reaches Windows (new desktop), and no "release" follows


def test_ctrl_win_space_is_hands_free_and_windows_never_gets_the_space():
    # Wispr Flow's hands-free chord; on the dev laptop the Copilot key is remapped to it with PowerToys.
    m = Matcher(parse_hotkey("ctrl+win"))
    assert feed(m, ("down", LCTRL), ("down", LWIN), ("down", SPACE), ("down", SPACE), ("up", SPACE), ("up", LWIN),
                ("up", LCTRL)) == [(False, None), (False, "press"), (True, "handsfree"), (True, None), (True, None),
                                   (False, None), (False, None)]


def test_space_is_typed_normally_when_ctrl_win_is_not_held():
    m = Matcher(parse_hotkey("ctrl+win"))
    assert feed(m, ("down", SPACE), ("up", SPACE)) == [(False, None), (False, None)]


def test_ctrl_win_with_another_modifier_is_not_the_hotkey():
    m = Matcher(parse_hotkey("ctrl+win"))
    assert [e for _, e in feed(m, ("down", LSHIFT), ("down", LCTRL), ("down", LWIN))] == [None, None, None]


def test_ctrl_win_while_a_letter_is_held_is_not_the_hotkey():
    m = Matcher(parse_hotkey("ctrl+win"))
    assert [e for _, e in feed(m, ("down", LEFT), ("down", LCTRL), ("down", LWIN))] == [None, None, None]


def test_tap_again_after_release_presses_again():
    m = Matcher(parse_hotkey("ctrl+win"))
    feed(m, ("down", LCTRL), ("down", LWIN), ("up", LWIN), ("up", LCTRL))
    assert feed(m, ("down", LCTRL), ("down", LWIN))[-1] == (False, "press")


# ---------------------------------------------------------------- the Menu key

def test_menu_key_is_hidden_from_apps():
    m = Matcher(parse_hotkey("menu"))
    assert feed(m, ("down", MENU), ("down", MENU), ("up", MENU)) == [(True, "press"), (True, None), (True, "release")]


def test_menu_key_with_a_modifier_is_left_alone():
    m = Matcher(parse_hotkey("menu"))
    assert feed(m, ("down", LSHIFT), ("down", MENU), ("up", MENU)) == [(False, None), (False, None), (False, None)]


# ---------------------------------------------------------------- modifiers + key

def test_ctrl_alt_d_hides_only_the_d():
    m = Matcher(parse_hotkey("ctrl+alt+d"))
    assert feed(m, ("down", LCTRL), ("down", LALT), ("down", D), ("up", D), ("up", LALT), ("up", LCTRL)) == [
        (False, None), (False, None), (True, "press"), (True, "release"), (False, None), (False, None)]


def test_plain_d_is_typed_normally():
    m = Matcher(parse_hotkey("ctrl+alt+d"))
    assert feed(m, ("down", D), ("up", D)) == [(False, None), (False, None)]


# ---------------------------------------------------------------- Esc

def test_esc_cancels_only_while_recording():
    m = Matcher(parse_hotkey("ctrl+win"))
    assert feed(m, ("down", VK_ESCAPE), ("up", VK_ESCAPE)) == [(False, None), (False, None)]
    m.recording = True
    assert feed(m, ("down", VK_ESCAPE), ("down", VK_ESCAPE), ("up", VK_ESCAPE)) == [
        (True, "cancel"), (True, None), (True, None)]


# ---------------------------------------------------------------- missed key releases

def test_keys_released_while_the_hook_was_blind_are_forgotten():
    # Win+L: the hook sees Win go down but not up (the lock screen takes over).
    held = {LWIN}
    m = Matcher(parse_hotkey("ctrl+win"), is_held=lambda vk: vk in held)
    feed(m, ("down", LWIN))
    held.clear()  # back from the lock screen, Win is no longer down
    assert feed(m, ("down", LCTRL))[-1] == (False, None)  # Ctrl alone must not look like Ctrl+Win
