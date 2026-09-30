"""sst: record from the microphone and transcribe it locally.

  uv run sst app                   the tray app: hold Ctrl+Win in any app, speak, let go: the text is typed there
  uv run sst dictate               the same in this console window
  uv run sst start                 record, press Enter to stop, print the text (repeats until you quit)
  uv run sst file <audio.wav>      transcribe an existing WAV file
  uv run sst devices               list microphones (* = default)
  uv run sst bench <folder>        score a reading test recorded with the tray app
  uv run sst web                   open a Record / Stop page in the browser

Options: --engine parakeet   --device <number from `sst devices`>
"""
import argparse
import logging
import sys
import time
from pathlib import Path

from sst import __version__
from sst.audio import list_input_devices, load_wav, record_until_enter, save_recording
from sst.engines import ENGINES, load_engine


def _load(engine_name: str):
    print(f"Loading {engine_name} model...", end=" ", flush=True)
    t0 = time.perf_counter()
    engine = load_engine(engine_name)
    print(f"ready ({time.perf_counter() - t0:.1f}s)")
    return engine


def _transcribe_and_report(engine, audio, rate) -> str:
    duration = len(audio) / rate
    t0 = time.perf_counter()
    text = engine.transcribe(audio, rate)
    took = time.perf_counter() - t0
    print(f"\n  Text: {text or '(nothing recognised)'}")
    print(f"  [{duration:.1f}s of audio transcribed in {took:.2f}s by {engine.name}]")
    return text


def cmd_start(args) -> None:
    engine = _load(args.engine)  # load before recording so there is no wait after you stop talking

    while True:
        print("\n● Recording... speak now, then press Enter to stop.")
        audio, rate = record_until_enter(args.device)

        if len(audio) < rate * 0.3:
            print("  Too short, nothing to transcribe.")
        else:
            peak = float(abs(audio).max())
            if peak < 0.01:
                print("  Warning: almost silent. Check that the right microphone is selected (`sst devices`).")
            text = _transcribe_and_report(engine, audio, rate)
            print(f"  Saved: recordings\\{save_recording(audio, rate, text)}.wav / .txt")

        if input("\nPress Enter to record again, or type q then Enter to quit: ").strip().lower() == "q":
            break


def cmd_file(args) -> None:
    path = Path(args.path)
    audio, rate = load_wav(path)
    engine = _load(args.engine)
    _transcribe_and_report(engine, audio, rate)


def cmd_devices(args) -> None:
    print("\n".join(list_input_devices()))


def cmd_dictate(args) -> None:
    from sst.dictate import run

    run(lambda: _load(args.engine), hotkey=args.hotkey, device=args.device, save=not args.no_save)


def cmd_app(args) -> None:
    from sst.app import main as app_main

    sys.exit(app_main())


def cmd_bench(args) -> None:
    from sst import bench
    from sst.gateway import GatewayConfig, Polisher
    from sst.settings import Profiles, Settings

    profile = Profiles.load().current  # the models and words of the profile in use in the app
    settings, gateway = Settings.load(profile.settings_file), GatewayConfig.load(profile.gateway_file)
    models = args.model or [m for m in (settings.cleanup_model, settings.cleanup_fallback) if m]
    polishers = {f"Parakeet + {m}": Polisher(gateway, m, settings.vocabulary) for m in models} if gateway.address else {}
    results = bench.score(Path(args.folder), _load(args.engine), polishers, progress=lambda text: print(" ", text))
    print()
    print(results.report())


def cmd_web(args) -> None:
    from sst.web import serve

    serve(_load(args.engine), port=args.port, open_browser=not args.no_browser)


def main() -> None:
    parser = argparse.ArgumentParser(prog="sst", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"sst {__version__}")
    parser.add_argument("--engine", default="parakeet", choices=ENGINES)
    parser.add_argument("--device", type=int, default=None, help="microphone number from `sst devices`")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("app", help="the tray app with the recording indicator, settings and history")
    p_dictate = sub.add_parser("dictate", help="type what you say into any app, using a hotkey")
    p_dictate.add_argument("--hotkey", default="ctrl+win",
                           help="ctrl+win (default, like Wispr Flow), menu (the Menu key), or e.g. ctrl+alt+d")
    p_dictate.add_argument("--no-save", action="store_true", help="don't keep recordings in recordings/")
    sub.add_parser("start", help="record from the microphone and transcribe")
    p_file = sub.add_parser("file", help="transcribe a WAV file")
    p_file.add_argument("path")
    sub.add_parser("devices", help="list microphones")
    p_bench = sub.add_parser("bench", help="score a reading test folder (recorded with the tray app's Reading test)")
    p_bench.add_argument("folder")
    p_bench.add_argument("--model", action="append", help="cleanup model to compare (repeatable); default: the ones in Settings")
    p_web = sub.add_parser("web", help="open the record/stop page in your browser")
    p_web.add_argument("--port", type=int, default=8765)
    p_web.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="  %(message)s")  # e.g. the per-dictation timing line
    try:
        commands = {"app": cmd_app, "dictate": cmd_dictate, "start": cmd_start, "file": cmd_file,
                    "devices": cmd_devices, "bench": cmd_bench, "web": cmd_web}
        commands[args.command](args)
    except KeyboardInterrupt:
        print("\nStopped.")
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
