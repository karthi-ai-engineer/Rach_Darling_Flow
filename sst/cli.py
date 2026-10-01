"""sst: record from the microphone and transcribe it locally.

  uv run sst app                   the tray app: hold Ctrl+Win in any app, speak, let go: the text is typed there
  uv run sst dictate               the same in this console window
  uv run sst start                 record, press Enter to stop, print the text (repeats until you quit)
  uv run sst file <audio.wav>      transcribe an existing WAV file
  uv run sst devices               list microphones (* = default)
  uv run sst eval [<folder>...]    score reading tests (all of them by default); --degrade, --model, --no-cleanup
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
from sst.engines import DEFAULT_MODEL, ENGINES, load_engine, usable


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


def cmd_eval(args) -> None:
    from sst import bench, evaluate
    from sst.gateway import GatewayConfig, Polisher
    from sst.settings import Profiles, Settings

    profile = Profiles.load().current  # the models, words and tests of the profile in use in the app
    folders = [Path(f) for f in args.folders] or bench.sessions(profile.folder(bench.BENCH_DIR))
    if not folders:
        raise SystemExit("No reading tests yet: record one in Rflow's Reading test page, or give test folders.")
    settings, gateway = Settings.load(profile.settings_file), GatewayConfig.load(profile.gateway_file)
    models = [] if args.no_cleanup else args.model or [m for m in (settings.cleanup_model, settings.cleanup_fallback) if m]
    polishers = {m.rsplit("/", 1)[-1]: Polisher(gateway, m, settings.vocabulary) for m in models} if gateway.address else {}
    evaluate.pipelines_for(polishers, args.degrade or [])  # a typo in --degrade is reported before the slow model load
    engine = _load(args.engine or usable(settings.speech_model))  # by default the profile's speech model, as in the app
    pipelines = evaluate.pipelines_for(polishers, args.degrade or [], title=engine.title)
    if hasattr(engine, "language"):  # a model that knows many languages listens for the profile's choice
        engine.language = settings.speech_language
    if hasattr(engine, "words"):  # as in dictation: the recogniser listens for Your words
        chosen = [w.strip() for w in args.words.split(",")] if args.words else settings.vocabulary
        engine.words = [] if args.no_words else [w for w in chosen if w]
        print(f"  Your words used while recognising: {len(engine.words)}" + ("" if engine.biased else " (no bpe.vocab)"))
    results = evaluate.run(folders, engine, pipelines, progress=lambda text: print(" ", text))
    out = Path(args.out) if args.out else evaluate.output_folder(folders)
    results.save(out)
    print()
    print(results.report())
    print(f"Saved report.md and results.json in {out}")


def cmd_web(args) -> None:
    from sst.web import serve

    serve(_load(args.engine), port=args.port, open_browser=not args.no_browser)


def main() -> None:
    # A Windows console that isn't UTF-8 can't print every character of a report (e.g. "→"): show "?" rather than fail.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="sst", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"sst {__version__}")
    parser.add_argument("--engine", choices=ENGINES,
                        help="speech recognition model (default: parakeet; for eval, the one chosen in the app)")
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
    p_eval = sub.add_parser("eval", aliases=["bench"], help="score reading tests: recognition alone, degraded, with cleanup")
    p_eval.add_argument("folders", nargs="*", help="test folders (default: every test of the profile in use)")
    p_eval.add_argument("--model", action="append", help="cleanup model to compare (repeatable); default: the ones in Settings")
    p_eval.add_argument("--no-cleanup", action="store_true", help="recognition only, no cleanup models")
    p_eval.add_argument("--no-words", action="store_true", help="recognise without listening for Your words (hotwords)")
    p_eval.add_argument("--words", help='try other words instead of Your words, e.g. "Karthi,Vercel,CodeQL"')
    p_eval.add_argument("--degrade", action="append",
                        help="also score the audio made worse on purpose: narrowband (phone / Bluetooth call quality) "
                             "or gain:<dB>, e.g. gain:-20 (repeatable)")
    p_eval.add_argument("--out", help="where report.md and results.json go (default: the test's folder, or 'summary')")
    p_web = sub.add_parser("web", help="open the record/stop page in your browser")
    p_web.add_argument("--port", type=int, default=8765)
    p_web.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")

    args = parser.parse_args()
    if args.command not in ("eval", "bench"):  # eval takes the profile's own model when none is given
        args.engine = args.engine or DEFAULT_MODEL
    logging.basicConfig(level=logging.INFO, format="  %(message)s")  # e.g. the per-dictation timing line
    try:
        commands = {"app": cmd_app, "dictate": cmd_dictate, "start": cmd_start, "file": cmd_file,
                    "devices": cmd_devices, "eval": cmd_eval, "bench": cmd_eval, "web": cmd_web}
        commands[args.command](args)
    except KeyboardInterrupt:
        print("\nStopped.")
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
