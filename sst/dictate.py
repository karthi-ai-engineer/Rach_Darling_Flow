"""Dictate into any app: put the cursor in a text box, press the hotkey, speak, and the text
is typed where the cursor is.

  hold the hotkey      record while held; let go to stop    (push-to-talk)
  tap the hotkey       start recording; tap again to stop   (hands-free)
  Ctrl+Win+Space       hands-free too, as in Wispr Flow
  Esc while recording  cancel, nothing is typed

`Dictation` holds the logic and is shared by the console command (`run` below), the tray app
(sst.app) and the tests. Transcription and typing run on a worker thread, so a new recording
can start right away.
"""
import ctypes
import logging
import queue
import subprocess
import threading
import time
import winsound
from collections.abc import Callable

import numpy as np

from sst.audio import Recorder, Take, save_recording
from sst.hotkey import HotkeyListener, parse_hotkey
from sst.paste import paste_text

HOLD_SECONDS = 0.4   # key held longer than this = push-to-talk; a quicker tap = hands-free
MIN_SECONDS = 0.3    # shorter recordings are treated as accidental presses
MAX_SECONDS = 180    # recordings stop by themselves after 3 minutes (the text is still typed)
DEFAULT_HOTKEY = "ctrl+win"

log = logging.getLogger(__name__)


class Dictation:
    """Turns hotkey events into recordings and typed text.

    Feed it `handle(event, at)` for every hotkey event and `tick(now)` a few times a second.
    It reports progress through `on_state(state, message)`, where state is one of
    recording, transcribing, typed (message = the text), typed_raw (typed as heard because the cleanup
    couldn't help; message = the reason), typed_local (a cloud speech model couldn't help, so Parakeet transcribed on
    this computer; message = the reason), idle, cancelled, ignored, warning, error; and each typed text
    through `on_result(heard, typed, seconds of audio)`. Both are called from the caller's thread and from the worker
    thread.
    `cleanup` is an optional sst.gateway.Polisher (or anything with prepare(), polish(text) and last_error). An engine
    may have prepare() too (connect while the user speaks) and last_error (why it fell back on another engine).
    """

    def __init__(self, engine, recorder, *, paste: Callable[[str], None] = paste_text, sounds: bool = True,
                 save: bool = True):
        self.engine, self.recorder, self.paste = engine, recorder, paste
        self.sounds, self.save = sounds, save
        self.listener: HotkeyListener | None = None  # told when recording, so that Esc cancels only then
        self.cleanup = None  # set and replaced by the app when the chosen model changes; None = type what was heard
        self.on_state: Callable[[str, str], None] = lambda state, message: None
        self.on_result: Callable[[str, str, float], None] = lambda heard, typed, seconds: None
        self.recording = False
        self._holding = False  # the press that started this recording has not been released yet
        self._started = 0.0
        self._stopped_by_press = -1.0  # when a hotkey press last ended a recording
        self._jobs: queue.Queue[Take] = queue.Queue()
        threading.Thread(target=self._work, name="transcriber", daemon=True).start()

    def handle(self, event: str, at: float) -> None:
        if event == "press":
            if self.recording:
                self._stop(keep=True)
                self._stopped_by_press = at
            else:
                self._start(at)
        elif event == "handsfree":  # Ctrl+Win+Space, arriving right after that chord's "press"
            if self.recording:
                self._holding = False  # keep recording after the keys are let go
            elif at - self._stopped_by_press > 1.0:  # not the chord that has just stopped a recording
                self._start(at)
                self._holding = False
        elif not self.recording:
            return
        elif event == "release" and self._holding:
            self._holding = False
            if at - self._started >= HOLD_SECONDS:
                self._stop(keep=True)
        elif event == "cancel":
            self._stop(keep=False)
        elif event == "interrupt" and self._holding:  # it was a shortcut such as Ctrl+Win+D, not dictation
            self._stop(keep=False, quiet=True)

    def tick(self, now: float) -> None:
        if self.recording and now - self._started > MAX_SECONDS:
            self.on_state("warning", f"Reached {MAX_SECONDS // 60} minutes, stopping.")
            self._stop(keep=True)
        tick = getattr(self.recorder, "tick", None)
        if tick:
            tick(now)  # close a warm microphone that has been idle long enough

    def close(self) -> None:
        if self.recording:
            self._stop(keep=False, quiet=True)
        close = getattr(self.recorder, "close", None)
        if close:
            close()

    def wait(self) -> None:
        """Block until every recording so far is transcribed and typed."""
        self._jobs.join()

    def _start(self, at: float) -> None:
        try:
            self.recorder.start()
        except Exception as e:
            log.exception("Could not open the microphone")
            self.on_state("error", f"Could not open the microphone: {e}")
            return
        self._beep(880)
        prepare = getattr(self.engine, "prepare", None)
        if prepare:
            prepare()  # a cloud speech model: connect while the user speaks, not after
        if self.cleanup:
            self.cleanup.prepare()  # connect to the gateway while the user speaks, not after
        if self.listener:
            self.listener.recording = True  # Esc now cancels
        self.recording, self._holding, self._started = True, True, at
        self.on_state("recording", "")

    def _stop(self, keep: bool, quiet: bool = False) -> None:
        self.recording = False
        if self.listener:
            self.listener.recording = False  # give Esc back to the other apps
        # The take keeps recording a short tail after the key release; the worker waits for it, not this thread.
        stop_later = getattr(self.recorder, "stop_later", None)
        take = stop_later() if stop_later else Take.ready(self.recorder.stop(), self.recorder.rate)
        if quiet:
            self.on_state("idle", "")
        elif not keep:
            self._beep(330)
            self.on_state("cancelled", "Cancelled.")
        elif take.seconds < MIN_SECONDS:
            self.on_state("ignored", "Too short, ignored.")
        else:
            self._beep(660)
            self._jobs.put(take)
            self.on_state("transcribing", "")

    def _work(self) -> None:
        while True:
            take = self._jobs.get()
            try:
                audio, rate = take.audio(), take.rate
                t0 = time.perf_counter()
                engine = self.engine  # read once: the app may swap it meanwhile
                try:
                    text = engine.transcribe(audio, rate)
                except Exception as e:
                    if self.save:  # e.g. a cloud model failed with no Parakeet to take over: the voice isn't lost
                        save_recording(audio, rate, "")
                        raise RuntimeError(f"{e}. The recording is kept in Rflow's recordings folder.") from e
                    raise
                fell_back = getattr(engine, "last_error", "")
                took = time.perf_counter() - t0
                log.info("%.1fs -> %.2fs  %s", len(audio) / rate, took, text or "(nothing recognised)")
                cleanup = self.cleanup  # read once: the app may swap it meanwhile
                typed = cleanup.polish(text) if cleanup and text else text
                if typed:
                    self.paste(typed + " ")  # trailing space so the next dictation doesn't run into this one
                if float(np.abs(audio).max()) < 0.01:
                    self.on_state("warning", "Almost silent: check the microphone.")
                if self.save:
                    save_recording(audio, rate, typed)
                if typed:
                    self.on_result(text, typed, len(audio) / rate)
                if typed and fell_back:
                    self.on_state("typed_local", fell_back)
                elif typed and cleanup and cleanup.last_error:
                    self.on_state("typed_raw", cleanup.last_error)
                else:
                    self.on_state("typed" if typed else "idle", typed)
            except Exception as e:  # keep the worker alive for the next recording
                log.exception("Transcription or typing failed")
                self.on_state("error", f"Error: {e}")
            finally:
                self._jobs.task_done()

    def _beep(self, frequency: int) -> None:
        if self.sounds:
            threading.Thread(target=winsound.Beep, args=(frequency, 60), daemon=True).start()


# ---- the console command: `sst dictate`

def run(load_engine: Callable, hotkey: str = DEFAULT_HOTKEY, device: int | str | None = None, save: bool = True) -> None:
    key = parse_hotkey(hotkey)  # a typo is reported before the slow model load
    if already_running():
        raise SystemExit("Dictation is already running in another window (the installed app or dictate.cmd).")
    if key.modifiers == {"ctrl", "win"} and key.key is None and wispr_flow_running():
        print("Warning: Wispr Flow is running and also listens to Ctrl+Win, so both would type.\n"
              "         Quit Wispr Flow (its tray icon -> Quit), or start this with --hotkey menu.")
    dictation = Dictation(load_engine(), Recorder(device), save=save)
    dictation.on_state = _print_state
    listener = HotkeyListener(key)  # started after the model load, so typing never waits for it
    dictation.listener = listener
    listener.start()
    print_ready(key)
    try:
        while True:
            try:
                event, at = listener.events.get(timeout=0.25)
                dictation.handle(event, at)
            except queue.Empty:
                pass
            dictation.tick(time.monotonic())
    finally:
        listener.stop()
        dictation.close()


def print_ready(key) -> None:
    rows = [(f"hold {key.label}", "talk while holding, let go to type the text"),
            (f"tap  {key.label}", "start recording, tap again to stop and type the text")]
    if key.key is None:
        rows.append((f"{key.label}+Space", "hands-free as well (like Wispr Flow)"))
    rows.append(("Esc", "cancel the recording"))
    width = max(len(keys) for keys, _ in rows)
    print("\nReady. Click in any text box, then:\n"
          + "".join(f"  {keys:<{width}}   {what}\n" for keys, what in rows)
          + "Keep this window open (you can minimise it). Close it or press Ctrl+C to quit.\n")


def _print_state(state: str, message: str) -> None:
    if state == "recording":
        print("● Recording...")
    elif state in ("cancelled", "ignored", "warning", "error"):
        print(f"  {message}")


def already_running() -> bool:
    # A named mutex shared by every copy (tray app, console, source checkout); Windows frees it when the process ends.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "SST-Dictation-dictate")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def wispr_flow_running() -> bool:
    try:
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Wispr Flow.exe", "/NH"], capture_output=True,
                                text=True, errors="replace", timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "Wispr Flow.exe" in result.stdout
