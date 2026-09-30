# Handoff

Where the project stands, so work can continue on any device. Updated at the end of every phase.

_Last updated: 2026-09-29_

## Status

| Phase | What | State |
|---|---|---|
| 0 | Record and transcribe locally: CLI (`sst start`, `sst file`) and web page (`sst web`) | done, on `main` |
| 1 | Dictate into any app with a global hotkey (`sst dictate`, Ctrl+Alt+D) | PR #2, waiting for merge |
| setup | GitHub CI (lint, tests, CodeQL), Dependabot, templates, CLAUDE.md, this file | this PR |
| 2 | Windows installer (`Setup.exe`, no Python needed) and a release pipeline | in progress |

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
- **Hotkey Ctrl+Alt+D:** Ctrl+Alt+Space is already taken on the dev laptop (probably by the Claude desktop app).
  Ctrl+Shift+Space and F9 would steal VS Code, JetBrains or Excel shortcuts. `--hotkey` changes it.
- **Tap = hands-free, hold = push-to-talk** on the same key, split at 0.4 s. Esc cancels, and Esc is only taken from
  other apps while recording.
- **Typing = clipboard + Ctrl+V.** The old clipboard is restored afterwards (every memory-block format) and dictated
  text is kept out of Win+V history. Pasting was chosen over simulated typing because it is instant and editors don't
  auto-close brackets partway through.
- **Long audio is split at pauses into pieces of up to 30 s.** Parakeet crashed in onnxruntime on a 514 s recording.
- **Recordings stop by themselves after 3 minutes** (the owner's choice). 3 minutes transcribes in about 23 s.
- **Recordings are kept** as `.wav` + `.txt` in `recordings/` (git-ignored) to compare engines later. `--no-save` turns
  this off.

## Known limitations

- Apps running as administrator don't receive the text, because Windows blocks input from normal programs into them.
- The console window is the only UI: there is no tray icon or on-screen recording indicator yet.
- English only. Punctuation and capitals come from the model as they are; there is no cleanup of filler words.

## Next steps

1. Phase 2: ship `Setup.exe` (PyInstaller + Inno Setup, per-user install without admin, model included, works offline),
   with a CI build and a tag-triggered GitHub Release.
2. Ideas for later phases, in rough order:
   - an on-screen recording indicator
   - a tray icon and start at login
   - hold Ctrl+Win like Wispr Flow (needs a low-level keyboard hook)
   - spacing and capitals that fit the text around the cursor
   - cleanup of the transcript (filler words, punctuation)
   - a custom vocabulary
