"""Dictate into any app: put the cursor in a text box, press the hotkey, speak, and the text
is typed where the cursor is.

  tap the hotkey       start recording; tap again to stop   (hands-free)
  hold the hotkey      record while held; let go to stop    (push-to-talk)
  Esc while recording  cancel, nothing is typed

Transcription and pasting run on a worker thread, so a new recording can start right away.
"""
import queue
import threading
import time
import winsound
from collections.abc import Callable

import numpy as np

from sst.audio import Recorder, save_recording
from sst.hotkey import VK_ESCAPE, is_key_down, parse_hotkey, register, unregister, wait_for_hotkey
from sst.paste import paste_text

HOLD_SECONDS = 0.4   # key held longer than this = push-to-talk; a quicker tap = hands-free
MIN_SECONDS = 0.3    # shorter recordings are treated as accidental presses
MAX_SECONDS = 180    # recordings stop by themselves after 3 minutes (the text is still typed)
TOGGLE_ID, CANCEL_ID = 1, 2


def run(load_engine: Callable, hotkey: str = "ctrl+alt+d", device: int | None = None, save: bool = True) -> None:
    # Claim the hotkey before the slow model load, so a typo or a clash is reported straight away.
    modifiers, vk = parse_hotkey(hotkey)
    if not register(TOGGLE_ID, modifiers, vk):
        raise SystemExit(f"{hotkey} is already used by another app (or sst dictate is already running).\n"
                         "Pick another one, e.g.  uv run sst dictate --hotkey ctrl+alt+x")
    try:
        _listen(load_engine(), hotkey, vk, device, save)
    finally:
        unregister(TOGGLE_ID)
        unregister(CANCEL_ID)


def _listen(engine, hotkey: str, vk: int, device: int | None, save: bool) -> None:
    jobs: queue.Queue[tuple[np.ndarray, int]] = queue.Queue()
    threading.Thread(target=_transcribe_and_paste, args=(engine, jobs, save), daemon=True).start()
    recorder = Recorder(device)
    recording = False
    holding = False  # the press that started this recording has not been released yet
    started = 0.0

    def stop(keep: bool) -> None:
        nonlocal recording
        recording = False
        unregister(CANCEL_ID)  # give Esc back to the other apps
        audio = recorder.stop()
        seconds = len(audio) / recorder.rate
        if not keep:
            _beep(330)
            print("  Cancelled.")
        elif seconds < MIN_SECONDS:
            print("  Too short, ignored.")
        else:
            _beep(660)
            jobs.put((audio, recorder.rate))

    print(f"\nReady. Click in any text box, then:\n"
          f"  tap  {hotkey}   start recording, tap again to stop and type the text\n"
          f"  hold {hotkey}   talk while holding, let go to type the text\n"
          f"  {'Esc':<{len(hotkey) + 5}}   cancel the recording\n"
          f"Keep this window open (you can minimise it). Close it or press Ctrl+C to quit.\n")
    try:
        while True:
            pressed = wait_for_hotkey(0.03 if recording else 0.25)
            now = time.monotonic()
            if not recording:
                if pressed == TOGGLE_ID:
                    try:
                        recorder.start()
                    except Exception as e:
                        print(f"  Could not open the microphone: {e}")
                        continue
                    _beep(880)
                    register(CANCEL_ID, 0, VK_ESCAPE)
                    recording, holding, started = True, True, now
                    print("● Recording...")
            elif pressed == CANCEL_ID:
                stop(keep=False)
            elif pressed == TOGGLE_ID:
                stop(keep=True)
            elif holding and not is_key_down(vk):
                holding = False
                if now - started >= HOLD_SECONDS:
                    stop(keep=True)
            elif now - started > MAX_SECONDS:
                print(f"  Reached {MAX_SECONDS // 60} minutes, stopping.")
                stop(keep=True)
    finally:
        recorder.stop()


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
