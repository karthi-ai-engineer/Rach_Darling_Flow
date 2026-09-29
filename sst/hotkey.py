"""Global hotkeys on Windows through a low-level keyboard hook (the way Wispr Flow does it).

A hotkey is modifiers plus an optional key:
  ctrl+win     modifiers held together on their own    (default, like Wispr Flow)
  menu         the Menu key, next to right Alt         its right-click menu is blocked
  ctrl+alt+d   modifiers + a key                       the key is blocked, the modifiers pass through

The hook runs on its own thread and reports "press", "release", "cancel" (Esc while recording),
"handsfree" (Space added to a modifier-only hotkey: Ctrl+Win+Space, as in Wispr Flow) and "interrupt"
(any other key added, e.g. Ctrl+Win+D: a Windows shortcut, not dictation) on a queue.
"""
import ctypes
import queue
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WH_KEYBOARD_LL, HC_ACTION, WM_QUIT = 13, 0, 0x0012
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
VK_ESCAPE, VK_SPACE, VK_MASK = 0x1B, 0x20, 0xE8  # 0xE8 is unassigned: pressing it tells Windows "Win was used with another key"
OUR_INPUT = 0x53535431  # dwExtraInfo on keys we send ourselves, so the hook lets them through untouched


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # only here so INPUT has the size SendInput expects
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _Union(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _Union)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x2


def send_keys(events: list[tuple[int, bool]]) -> None:
    """Press/release keys as (virtual-key code, is_release), marked so our own hook ignores them."""
    inputs = (INPUT * len(events))(*(
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP if up else 0, dwExtraInfo=OUR_INPUT))
        for vk, up in events))
    user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))


# The hook reports left/right variants (0xA0-0xA5, 0x5B/0x5C); the generic codes appear in injected input.
MODIFIER_OF = {0x10: "shift", 0xA0: "shift", 0xA1: "shift", 0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
               0x12: "alt", 0xA4: "alt", 0xA5: "alt", 0x5B: "win", 0x5C: "win"}
_MODIFIER_NAMES = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "win": "win"}
_KEYS = {"menu": 0x5D, "apps": 0x5D, "space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "home": 0x24,
         "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "pause": 0x13, "scrolllock": 0x91,
         "muhenkan": 0x1D, "henkan": 0x1C}  # 無変換 / 変換 on Japanese keyboards
_KEYS.update({f"f{n}": 0x6F + n for n in range(1, 25)})
_KEYS.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})


@dataclass(frozen=True)
class Hotkey:
    text: str
    modifiers: frozenset[str]
    key: int | None  # None: the modifiers alone are the hotkey

    @property
    def label(self) -> str:
        return "+".join("Menu key" if part == "menu" else part.capitalize() for part in self.text.split("+"))


def parse_hotkey(text: str) -> Hotkey:
    """'ctrl+win', 'menu' or 'ctrl+alt+d' -> Hotkey."""
    parts = [part.strip().lower() for part in text.split("+")]
    modifiers, key = set(), None
    for i, part in enumerate(parts):
        if part in _MODIFIER_NAMES:
            modifiers.add(_MODIFIER_NAMES[part])
        elif part in _KEYS and i == len(parts) - 1:
            key = _KEYS[part]
        else:
            raise ValueError(f"unknown key '{part}' in hotkey '{text}'. Use e.g. ctrl+win, menu or ctrl+alt+d")
    if key is None and len(modifiers) < 2:
        raise ValueError(f"'{text}' alone would get in the way of normal typing; combine two modifiers (e.g. ctrl+win)")
    return Hotkey("+".join(parts), frozenset(modifiers), key)


class Matcher:
    """Decides, for each physical key event, whether the hotkey went down or up and whether other apps
    should see the event. No Windows calls, so every case can be tested without a keyboard."""

    def __init__(self, hotkey: Hotkey, is_held: Callable[[int], bool] | None = None):
        self.hotkey = hotkey
        self.recording = False  # set by the dictation loop: Esc only cancels while recording
        self._is_held = is_held  # asks Windows whether a key is really down
        self._down: set[int] = set()  # keys physically held now
        self._blocked: set[int] = set()  # keys whose repeats and release must stay hidden too
        self._active = False  # the hotkey is held

    def feed(self, vk: int, is_down: bool) -> tuple[bool, str | None]:
        """Returns (hide the event from other apps, event or None)."""
        if is_down and self._is_held:
            # The hook misses releases on the lock screen (Win+L) and in admin windows; drop keys that were let go.
            self._down = {k for k in self._down if k == vk or self._is_held(k)}
            self._blocked &= self._down
            if self._active and not (self.hotkey.key in self._down if self.hotkey.key else self._hotkey_held()):
                self._active = False
        repeat = is_down and vk in self._down
        (self._down.add if is_down else self._down.discard)(vk)

        if vk in self._blocked:
            if is_down:
                return True, None
            self._blocked.discard(vk)
            if self._active and vk == self.hotkey.key:
                self._active = False
                return True, "release"
            return True, None
        if repeat:
            return False, None
        if is_down and vk == VK_ESCAPE and self.recording:
            self._blocked.add(vk)
            return True, "cancel"
        if self.hotkey.key is not None:
            if is_down and vk == self.hotkey.key and self._held_modifiers() == self.hotkey.modifiers:
                self._blocked.add(vk)
                self._active = True
                return True, "press"
            return False, None
        return self._modifier_only(vk, is_down)

    def _modifier_only(self, vk: int, is_down: bool) -> tuple[bool, str | None]:
        wanted = self.hotkey.modifiers
        if is_down:
            if self._active and vk == VK_SPACE:
                self._active = False  # Ctrl+Win+Space: hands-free, as in Wispr Flow; Windows doesn't get the Space
                self._blocked.add(vk)
                return True, "handsfree"
            if self._active and MODIFIER_OF.get(vk) not in wanted:
                self._active = False  # another key joined: it's a shortcut like Ctrl+Win+D, not dictation
                return False, "interrupt"
            if not self._active and self._held_modifiers() == wanted and all(k in MODIFIER_OF for k in self._down):
                self._active = True
                return False, "press"
        elif self._active and MODIFIER_OF.get(vk) in wanted and MODIFIER_OF[vk] not in self._held_modifiers():
            self._active = False
            return False, "release"
        return False, None

    def _held_modifiers(self) -> frozenset[str]:
        return frozenset(MODIFIER_OF[vk] for vk in self._down if vk in MODIFIER_OF)

    def _hotkey_held(self) -> bool:
        return self.hotkey.modifiers <= self._held_modifiers()


class HotkeyListener:
    """Runs the keyboard hook on its own thread; events arrive on `events` as (event, time.monotonic())."""

    def __init__(self, hotkey: Hotkey):
        self.hotkey = hotkey
        self.events: queue.Queue[tuple[str, float]] = queue.Queue()
        self._matcher = Matcher(hotkey, is_held=lambda vk: bool(user32.GetAsyncKeyState(vk) & 0x8000))
        # Pressing Win or Alt with no other key opens the Start menu / an app's menu bar on release.
        self._mask = bool(hotkey.modifiers & {"win", "alt"})
        self._thread_id = 0
        self._ready = threading.Event()
        self._error = ""
        self._proc = HOOKPROC(self._on_key)  # keep a reference: Windows calls it for as long as the hook lives

    @property
    def recording(self) -> bool:
        return self._matcher.recording

    @recording.setter
    def recording(self, value: bool) -> None:
        self._matcher.recording = value

    def start(self) -> None:
        threading.Thread(target=self._run, name="keyboard-hook", daemon=True).start()
        self._ready.wait(5)
        if self._error:
            raise OSError(self._error)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self._error = f"could not install the keyboard hook (Windows error {ctypes.get_last_error()})"
        self._ready.set()
        if not hook:
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:  # Windows calls the hook from in here
                pass
        finally:
            user32.UnhookWindowsHookEx(hook)

    def _on_key(self, code: int, wparam: int, lparam: int) -> int:
        try:
            if code == HC_ACTION:
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if info.dwExtraInfo != OUR_INPUT:
                    hide, event = self._matcher.feed(info.vkCode, wparam in (WM_KEYDOWN, WM_SYSKEYDOWN))
                    if event:
                        if event == "press" and self._mask:
                            send_keys([(VK_MASK, False), (VK_MASK, True)])
                        self.events.put((event, time.monotonic()))
                    if hide:
                        return 1
        except Exception as e:  # never let an error here swallow or delay the user's typing
            print(f"  Keyboard hook error: {e}")
        return user32.CallNextHookEx(None, code, wparam, lparam)
