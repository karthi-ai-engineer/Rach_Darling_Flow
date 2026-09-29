"""Global hotkeys on Windows. They work whichever app has focus, and the key press is swallowed
so it does not also reach that app (e.g. Ctrl+Alt+D does not type a d)."""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD]
user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, PM_REMOVE, QS_ALLINPUT = 0x0312, 0x1, 0x04FF
VK_ESCAPE = 0x1B

_MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "home": 0x24, "end": 0x23,
         "pageup": 0x21, "pagedown": 0x22, "pause": 0x13, "scrolllock": 0x91}
_KEYS.update({f"f{n}": 0x6F + n for n in range(1, 25)})
_KEYS.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})


def parse_hotkey(text: str) -> tuple[int, int]:
    """'ctrl+alt+space' -> (modifier flags, virtual-key code)."""
    *modifiers, key = [part.strip().lower() for part in text.split("+")]
    try:
        flags = 0
        for name in modifiers:
            flags |= _MODIFIERS[name]
        return flags, _KEYS[key]
    except KeyError as e:
        raise ValueError(f"unknown key {e} in hotkey '{text}'. Use e.g. ctrl+alt+d, ctrl+alt+x or f8") from None


def register(hotkey_id: int, modifiers: int, vk: int) -> bool:
    """Deliver this key combination to the calling thread. False if another app already uses it."""
    return bool(user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk))


def unregister(hotkey_id: int) -> None:
    user32.UnregisterHotKey(None, hotkey_id)


def wait_for_hotkey(timeout: float) -> int | None:
    """Wait up to `timeout` seconds for a registered hotkey press on this thread.
    Returns its id, or None if none came. Short timeouts keep Ctrl+C responsive."""
    msg = wintypes.MSG()
    if not user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        user32.MsgWaitForMultipleObjects(0, None, False, int(timeout * 1000), QS_ALLINPUT)
        if not user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            return None
    return msg.wParam if msg.message == WM_HOTKEY else None


def is_key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)
