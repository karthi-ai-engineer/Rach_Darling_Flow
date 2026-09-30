# Handoff

Where the project stands, so work can continue on any device. Updated at the end of every phase.

_Last updated: 2026-09-29_

## Status

| Phase | What | State |
|---|---|---|
| 0 | Record and transcribe locally: CLI (`sst start`, `sst file`) and web page (`sst web`) | done, on `main` |
| 1 | Dictate into any app with a global hotkey (`sst dictate`, Ctrl+Alt+D) | PR #2, waiting for merge |
| setup | GitHub CI (lint, tests, CodeQL), Dependabot, templates, CLAUDE.md, this file | PR #4 (CI green), waiting for merge |
| 2 | Windows installer (`Setup.exe`, no Python needed), CI app build, release pipeline | PR #6, waiting for a test on another laptop + merge |
| 3 | Wispr-style hotkeys: hold Ctrl+Win, Ctrl+Win+Space hands-free, optional Menu key | PR #8, the owner confirmed it works (installed app, 2026-09-29), waiting for merge |

## Continue on another device

```
git clone https://github.com/karthi-ai-engineer/Rach_Darling_Flow.git
cd Rach_Darling_Flow
git config user.name "Karthi27"
git config user.email "karthi.ai.engineer@gmail.com"
gh auth login                                     # as karthi-ai-engineer
uv sync
uv run python scripts/download_model.py parakeet  # ~630 MB, not in git
uv run pytest
uv run sst dictate
```

Then read `CLAUDE.md` (workflow and rules) and pick up at **Next steps** below.

## Decisions so far

- **Engine:** Parakeet 0.6B "unified" English, int8, on the CPU via sherpa-onnx 1.13.8. It loads in about 2 s, and a
  3 s clip takes about 0.3 s. `sherpa-onnx-core` has to be listed explicitly, or Windows loads the older
  `System32\onnxruntime.dll` and the model fails.
- **Hotkey Ctrl+Win (phase 3), as in Wispr Flow.** Phase 1 used Ctrl+Alt+D; the owner's muscle memory is Ctrl+Win, and
  Ctrl+Win+D opened new virtual desktops. Modifier-only keys need a low-level keyboard hook (`sst/hotkey.py`), not
  RegisterHotKey:
  - The hook thread only matches keys; the logic is the pure `Matcher` class, which is fully unit-tested.
  - sherpa-onnx releases the GIL while decoding (another Python thread waited at most 21 ms during a 3 s decode), so
    typing never lags during a transcription.
  - Keys sst sends itself carry `dwExtraInfo = OUR_INPUT`, and the hook ignores them. Keys injected by other tools
    (PowerToys remaps) are treated like real ones.
  - A vkE8 "mask" key is sent on press, so releasing Win doesn't open Start (the same trick AutoHotkey uses).
  - Another key during Ctrl+Win means a Windows shortcut such as Ctrl+Win+D or +←/→, so the recording is dropped
    ("interrupt"). Space means hands-free and is hidden from Windows. The key-state tracking is re-checked with
    GetAsyncKeyState, because the hook misses releases on the lock screen.
  - Only one dictation can run (mutex `SST-Dictation-dictate`), and there's a warning if Wispr Flow is running.
- **The dev laptop's PowerToys Keyboard Manager** remaps Menu (0x5D) to Ctrl+Win and the Copilot key (Win+Shift+F23)
  to Ctrl+Win+Space, set up for Wispr Flow. With the default hotkey, both keys therefore work for sst as they are.
  Don't use `--hotkey menu` there: which hook sees the key first depends on start order.
- **Tap = hands-free, hold = push-to-talk** on the same key, split at 0.4 s. Esc cancels, and Esc is only taken from
  other apps while recording.
- **Typing = clipboard + Ctrl+V.** The old clipboard is restored afterwards (every memory-block format) and dictated
  text is kept out of Win+V history. Pasting was chosen over simulated typing because it is instant and editors don't
  auto-close brackets partway through.
- **Long audio is split at pauses into pieces of up to 30 s.** Parakeet crashed in onnxruntime on a 514 s recording.
- **Recordings stop by themselves after 3 minutes** (the owner's choice). 3 minutes transcribes in about 23 s.
- **Recordings are kept** as `.wav` + `.txt` in `recordings/` (git-ignored) to compare engines later. `--no-save` turns
  this off.

## Packaging (phase 2)

- `build_installer.cmd` runs `scripts/build_installer.py` in its own environment, `build\venv`, so it works while the
  dev copy is running. The steps are PyInstaller (one folder, `packaging/sst.spec`), then the model into
  `dist/sst/models` (hard links), then a smoke test (`dist/sst/sst.exe file` must transcribe the model's test WAV),
  then Inno Setup (`packaging/installer.iss`). It takes about 95 s and produces a 500 MB `Setup.exe`
  (710 MB installed).
- Installs per user into `%LOCALAPPDATA%\Programs\SST Dictation`, so no admin is needed. There is a Start menu
  shortcut, plus optional desktop and start-at-sign-in shortcuts. The model is inside, so it works offline.
- The installed app keeps recordings in `%LOCALAPPDATA%\sst\recordings` and its model next to `sst.exe`
  (see `sst/__init__.py`). Opening it with no arguments starts dictation (`packaging/sst_app.py`).
- sherpa-onnx's `onnxruntime.dll` must stay in `_internal/sherpa_onnx/lib/`. Verified: the installed app loads it
  from there, not the older copy in System32.
- The only DLL the bundle needs from Windows is `propsys.dll`, so no Microsoft C++ runtime install is needed.
- While running, the app holds the mutex `SST-Dictation-running`. Setup and uninstall use it to ask the user to close the app.
- Verified locally: silent install, install over an existing copy, the installed `sst.exe` transcribing, dictation
  mode, and uninstall (nothing left behind).
- The installer is not code-signed, so SmartScreen shows "Windows protected your PC" (click More info, then Run anyway).
  A certificate would fix that.
- Version: `__version__` in `sst/__init__.py` is the only place to change it. A tag `vX.Y.Z` must match it, or the
  Release workflow stops.
- CI: the `Build app (Windows)` job builds `sst.exe` on every PR and runs the smoke test (the model is cached in Actions).
  The Release workflow (tag `v*`, or started by hand) builds `Setup.exe` and publishes it as a GitHub Release (a manual
  run only uploads it as an artifact).
- Inno Setup on the dev laptop is a portable copy in `.tools/innosetup` (git-ignored).

## Known limitations

- Apps running as administrator don't receive the text, because Windows blocks input from normal programs into them.
- The console window is the only UI: there is no tray icon or on-screen recording indicator yet. A hotkey other
  than Ctrl+Win needs `sst.exe dictate --hotkey ...` in the shortcut; a settings file would be friendlier.
- Ctrl+Win isn't sent through the real hook in automated tests (Wispr Flow on the dev laptop would react). The hook
  plumbing is tested with the Menu key, Esc and Ctrl+Alt+X, and Ctrl+Win by the Matcher unit tests. The owner
  confirmed that Ctrl+Win dictation works in the installed app.
- English only. Punctuation and capitals come from the model as they are; there is no cleanup of filler words.

## Next steps

1. Merge in order: #2, #4, #6, then #8 (use "Create a merge commit"). Then tag `v0.1.0` on `main` and push the tag,
   and the Release workflow publishes the first installer.
2. Try `Setup.exe` on a second laptop. That is the one check not done yet (so far it was tested on the dev laptop only).
3. Ideas for later phases, in rough order:
   - an on-screen recording indicator
   - a tray icon and start at login
   - spacing and capitals that fit the text around the cursor
   - cleanup of the transcript (filler words, punctuation)
   - a custom vocabulary
