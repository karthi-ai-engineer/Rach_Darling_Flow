"""Dictate into any app: put the cursor in a text box, press the hotkey, speak, and the text
is typed where the cursor is.

  hold the hotkey      record while held; let go to stop    (push-to-talk)
  tap the hotkey       start recording; tap again to stop   (hands-free)
  Ctrl+Win+Space       hands-free too, as in Wispr Flow
  Esc while recording  cancel, nothing is typed

The default hotkey is Ctrl+Win, like Wispr Flow. Transcription and pasting run on a worker
thread, so a new recording can start right away.
"""
import ctypes
import queue
import subprocess
import threading
import time
import winsound
from collections.abc import Callable

import numpy as np

from sst.audio import Recorder, save_recording
from sst.hotkey import HotkeyListener, parse_hotkey
from sst.paste import paste_text

HOLD_SECONDS = 0.4   # key held longer than this = push-to-talk; a quicker tap = hands-free
MIN_SECONDS = 0.3    # shorter recordings are treated as accidental presses
MAX_SECONDS = 180    # recordings stop by themselves after 3 minutes (the text is still typed)
DEFAULT_HOTKEY = "ctrl+win"


def run(load_engine: Callable, hotkey: str = DEFAULT_HOTKEY, device: int | None = None, save: bool = True) -> None:
    key = parse_hotkey(hotkey)  # a typo is reported before the slow model load
    if _already_running():
        raise SystemExit("Dictation is already running in another window (the installed app or dictate.cmd).")
    if key.modifiers == {"ctrl", "win"} and key.key is None and _wispr_flow_running():
        print("Warning: Wispr Flow is running and also listens to Ctrl+Win, so both would type.\n"
              "         Quit Wispr Flow (its tray icon -> Quit), or start this with --hotkey menu.")
    engine = load_engine()
    listener = HotkeyListener(key)  # started after the model load, so typing never waits for it
    listener.start()
    try:
        _listen(engine, listener, device, save)
    finally:
        listener.stop()


def _listen(engine, listener: HotkeyListener, device: int | None, save: bool) -> None:
    jobs: queue.Queue[tuple[np.ndarray, int]] = queue.Queue()
    threading.Thread(target=_transcribe_and_paste, args=(engine, jobs, save), daemon=True).start()
    recorder = Recorder(device)
    recording = False
    holding = False  # the press that started this recording has not been released yet
    started = 0.0

    def start(at: float) -> None:
        nonlocal recording, holding, started
        try:
            recorder.start()
        except Exception as e:
            print(f"  Could not open the microphone: {e}")
            return
        _beep(880)
        listener.recording = True  # Esc now cancels
        recording, holding, started = True, True, at
        print("● Recording...")

    def stop(keep: bool, quiet: bool = False) -> None:
        nonlocal recording
        recording = False
        listener.recording = False  # give Esc back to the other apps
        audio = recorder.stop()
        seconds = len(audio) / recorder.rate
        if quiet:
            pass
        elif not keep:
            _beep(330)
            print("  Cancelled.")
        elif seconds < MIN_SECONDS:
            print("  Too short, ignored.")
        else:
            _beep(660)
            jobs.put((audio, recorder.rate))

    label = listener.hotkey.label
    rows = [(f"hold {label}", "talk while holding, let go to type the text"),
            (f"tap  {label}", "start recording, tap again to stop and type the text")]
    if listener.hotkey.key is None:
        rows.append((f"{label}+Space", "hands-free as well (like Wispr Flow)"))
    rows.append(("Esc", "cancel the recording"))
    width = max(len(keys) for keys, _ in rows)
    print("\nReady. Click in any text box, then:\n"
          + "".join(f"  {keys:<{width}}   {what}\n" for keys, what in rows)
          + "Keep this window open (you can minimise it). Close it or press Ctrl+C to quit.\n")
    stopped_by_press = -1.0  # when a hotkey press last ended a recording
    try:
        while True:
            try:
                event, at = listener.events.get(timeout=0.25)
            except queue.Empty:
                event, at = None, time.monotonic()
            if event == "press":
                if recording:
                    stop(keep=True)
                    stopped_by_press = at
                else:
                    start(at)
            elif event == "handsfree":  # Ctrl+Win+Space, arriving right after that chord's "press"
                if recording:
                    holding = False  # keep recording after the keys are let go
                elif at - stopped_by_press > 1.0:  # not the chord that has just stopped a recording
                    start(at)
                    holding = False
            elif not recording:
                continue
            elif event == "release" and holding:
                holding = False
                if at - started >= HOLD_SECONDS:
                    stop(keep=True)
            elif event == "cancel":
                stop(keep=False)
            elif event == "interrupt" and holding:  # it was a shortcut such as Ctrl+Win+D, not dictation
                stop(keep=False, quiet=True)
            elif time.monotonic() - started > MAX_SECONDS:
                print(f"  Reached {MAX_SECONDS // 60} minutes, stopping.")
                stop(keep=True)
    finally:
        recorder.stop()


def _already_running() -> bool:
    # A named mutex shared by every copy (installed app or source checkout); Windows frees it when the process ends.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "SST-Dictation-dictate")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def _wispr_flow_running() -> bool:
    try:
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Wispr Flow.exe", "/NH"], capture_output=True,
                                text=True, errors="replace", timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "Wispr Flow.exe" in result.stdout


def _transcribe_and_paste(engine, jobs: queue.Queue, save: bool) -> None:
    while True:
        audio, rate = jobs.get()
        try:
            t0 = time.perf_counter()
            text = engine.transcribe(audio, rate)
            took = time.perf_counter() - t0
            if text:
                paste_text(text + " ")  # trailing space so the next dictation doesn't run into this one
            print(f"  {len(audio) / rate:.1f}s -> {took:.2f}s  {text or '(nothing recognised)'}")
            if float(np.abs(audio).max()) < 0.01:
                print("  Warning: almost silent. Check the microphone (`uv run sst devices`, then --device N).")
            if save:
                save_recording(audio, rate, text)
        except Exception as e:  # keep the worker alive for the next recording
            print(f"  Error: {e}")


def _beep(frequency: int) -> None:
    threading.Thread(target=winsound.Beep, args=(frequency, 60), daemon=True).start()
