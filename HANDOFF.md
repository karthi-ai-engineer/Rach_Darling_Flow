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
| 4 | A real app: tray icon, recording pill, settings, history, logs (Qt) | PR #10, the owner uses it (pill, History), waiting for merge |
| 5 | AI text cleanup through the company gateway, model chosen in Settings; one-piece transcription up to 3 min | PR #12, waiting for a hands-on test + merge |

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

## The tray app (phase 4)

- `sst/app.py` (Qt / PySide6-Essentials, LGPL, bundled as separate DLLs). It has a tray icon and menu (status,
  History, Settings, logs folder, Quit), and the icon turns red while recording.
- The pill is a frameless, topmost, click-through window with `WS_EX_NOACTIVATE`. Checked: a focused window stays
  focused through every pill state, so the paste still goes to the user's app.
- The model loads on a background thread, and the tray icon appears at once. The keyboard hook starts after the model
  has loaded. A 15 ms QTimer pumps the hook events into `Dictation`, and the worker thread reports back through a Qt
  signal.
- `sst/dictate.py` `Dictation` is the one dictation engine, driven by the tray app and by the console command
  `sst dictate`. Its tests feed events with explicit times, so they are exact and fast (no sleeping).
- Settings (`%APPDATA%\sst\settings.json`) are the hotkey, microphone by name, sounds and saving recordings. A damaged
  file falls back to the defaults. History is `%APPDATA%\sst\history.jsonl` (last 200). Logs are
  `%LOCALAPPDATA%\sst\logs\sst.log` (rotating).
- The microphone list is re-read before every recording (~45 ms), so a headset plugged in later, or a new Windows
  default, is picked up. The dev laptop's default mic was Bluetooth earbuds (OnePlus Nord Buds 3r). Bluetooth headset
  mics use a low-quality call mode, so pick the laptop mic in Settings for better accuracy.
- The installer has two programs: `SST Dictation.exe` (tray app, no console; the shortcuts point here) and `sst.exe`
  (CLI).
- "Start with Windows" is on by default. The installer task and the in-app setting share one HKCU Run value with
  `--startup`, which starts quietly. Uninstall removes that value even if it was switched on from the app (verified
  with a throwaway installer).
- Build checks: `SST Dictation.exe --self-test` builds every window off-screen and transcribes once, and the build
  script runs it. The dependency scan of all 92 bundled binaries finds nothing missing on a plain Windows (Qt brings
  its own C++ runtime).
- The installer is 520 MB (Qt added 20 MB), 762 MB installed.
- The owner installed phase 4 on 2026-09-30 and dictated with it (the tray app, pill and History work).

## Accuracy on the owner's voice (first check, 2026-09-30)

- The owner read the "What I need from you" list aloud: 76.5 s, one hands-free dictation in the installed app.
  Compared with that text (148 words):

  | Decoding | Word errors |
  |---|---|
  | as dictated (greedy, cut into 30 s pieces) | 28% |
  | one piece | 24% |
  | beam search | no better |

  Leaving out the owner's own reading changes (skipped or added words), about 13–17% of words are wrong. This model
  scores about 6% on standard English benchmarks.
- Typical errors are tech words and names: commit → clot, "the five PRs" → "a file PR", Claude → cloud, Tamil →
  dropped / "Tamar", polishing → publishing, "tech words" → "that was". One whole sentence was dropped when cut at
  30 s, and decoded correctly as one piece.
- **Next fix:** raise `MAX_PIECE_SECONDS` in `sst/engines/parakeet.py`. 257 s in one piece works; it crashed at 514 s.
- Microphone: none is chosen, so the Windows default is used, which is the Bluetooth earbuds when they are connected.
  2 of that day's 6 recordings were phone quality (no sound above 4 kHz). The long reading was full band, so most
  errors are the model on this voice, not the mic.
- The analysis scripts were ad hoc. Phase 6 should add a proper reading test (`sst bench`) with reference texts.

## Company AI gateway (the next task, from the owner)

- `https://tw-gateway.twave.co.jp/v1` is OpenAI-compatible: models, chat/completions, responses, completions,
  embeddings, images/generations, audio/transcriptions. It is internal (172.16.5.107) and reachable from the dev laptop
  in about 50 ms.
- Auth is the owner's gateway API key, sent as `Authorization: Bearer <key>` or `X-API-Key: <key>`. The key lives only
  in `%APPDATA%\sst\gateway.json`, outside the repository and encrypted with DPAPI (see phase 5). **Never commit it.**
- Task:
  - list the models
  - test only the models hosted on the company's own GPU server, **one at a time** (a small server, with cold starts)
  - measure transcription accuracy and speed on the owner's recordings, and text polish quality and speed
  - pick the best and fastest, then use it in sst
- **Results (2026-09-30).** 24 models: 17 cloud (OpenAI, skipped as asked) and 7 local (`is_cloud: false`), tested one
  at a time:

  | Local model | Speed | First word | Polish (word error 23.6% before) | Transcription |
  |---|---|---|---|---|
  | Qwen/Qwen3-30B-A3B-Instruct-2507-FP8 | **43 tok/s** | 0.14–0.24 s | 22.9% (fixes "commit"; rewords a bit) | not supported |
  | unsloth/Qwen3.8-27B-NVFP4 | 28 tok/s | 0.24–0.35 s | 23.6%; short sentence 20% → **10%** (most faithful) | not listed |
  | Qwen/Qwen3.6-35B-A3B-FP8 | 20 tok/s | 1.3 s | 24.3% | HTTP 500 |
  | Qwen/Qwen-AgentWorld-35B-A3B | 26 tok/s | 0.26 s | 24.3%, identical output: same model as 3.6 | HTTP 500 |
  | Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf | 17.6 tok/s | 0.29 s | 25.0% (kept "clot") | not listed |
  | qwen3, sensenova-u1.5 | – | – | "Backend unavailable" (switched off; retried after 90 s) | – |

- **No local model transcribes speech.** Only the cloud `whisper-1` does. Keep Parakeet on the laptop for
  speech-to-text. vLLM can serve Whisper, so IT could add `whisper-large-v3-turbo` locally; worth asking.
- **Polish:**
  - With the owner's word list in the prompt, the LLMs fix names that sound like a vocabulary word ("STD" → "SST",
    "hashtag 2" → "#2", "clot" → "commit").
  - They can't bring back words the recognizer dropped, and they don't fix "cloud" → "Claude" when "cloud" also
    makes sense.
  - A typical one-sentence dictation polishes in 0.5–0.7 s; the 76 s reading takes 4–5 s.
- **Connection quirk:** from Python (not curl) the first connection to the gateway sometimes stalls. Use a 4 s
  connect timeout with retries, keep the connection alive, and warm it up at start. `trust_env=False`, since it's
  internal.
- The test scripts were ad hoc (httpx, one model per run). Phase 5 should turn them into `sst bench gateway`.

## Text cleanup (phase 5)

- The owner's decision: in Settings, choose **1. `unsloth/Qwen3.8-27B-NVFP4`** (best quality) or **2.
  `Qwen/Qwen3-30B-A3B-Instruct-2507-FP8`** (fastest), or Off. Saving activates it from the next dictation. Only the
  model changes; everything else stays the same.
- `sst/gateway.py`, standard library only (http.client, no proxy):
  - `Polisher.polish(text)` never raises. Each dictation gets 2 s plus 0.04 s per word to answer.
  - The other model is tried after an HTTP error. A timeout gives the heard text; there's no second wait.
  - An unreachable gateway (e.g. at home) is skipped for 60 s.
  - An implausible answer (much shorter or longer) is not used.
  - `<think>` tags and wrapping quotes are removed.
  - Connects with a 1.5 s timeout and 3 tries (the first-connect stall), keeps the connection alive, and
    `prepare()` connects when recording starts.
- `Dictation.cleanup` is swapped by the app on Save. The state `typed_raw` shows an amber pill, "Typed as heard",
  plus at most one tray notification per 10 minutes. History stores `heard` next to `text`.
- The key lives in `%APPDATA%\sst\gateway.json` (Settings edits it; masked field; "Test" button). It is never in git
  or the logs (`GatewayConfig.__repr__` masks it).
- On disk it is `api_key_protected`, encrypted with Windows DPAPI for the signed-in user: only that user on this laptop
  can decrypt it. CodeQL flagged the first version, which stored it as plain text. A plain `"api_key"` typed into the
  file by hand is encrypted on first load. A key that can't be decrypted (copied from another laptop) is dropped,
  and the user is asked to enter it again.
- Settings has a Settings button in the History window too; a tray click opens History, and the owner looked
  for the options there. `sst.exe` without arguments starts the tray app, because an old taskbar pin to `sst.exe`
  opened a console that said "already running".
- The system prompt: fix recognition errors using the user's vocabulary, add punctuation and capitals, remove
  fillers, keep the wording, don't answer. Settings' "Your words" feed the vocabulary.
- Against the real gateway:

  | | First cleanup | Next dictation | Connect |
  |---|---|---|---|
  | Model 1 | 1.12 s | 0.64 s | 1.6 s, while speaking (the stall happened and was hidden) |
  | Model 2 | 0.67 s | 0.35 s | 0.05 s |
  | Fallback (switched-off `qwen3` chosen) | 1.17 s via model 1 | | |

  "STD dictation" → "SST Dictation", "pr's" → "PRs", and fillers were removed.
- `MAX_PIECE_SECONDS` is 180: a whole dictation (max 3 min) is transcribed in one piece.
- Tests: 94. `tests/test_gateway.py` runs a fake gateway on localhost (good, error, slow, unreachable, implausible
  answers, keep-alive, fallback).

## Known limitations

- Apps running as administrator don't receive the text, because Windows blocks input from normal programs into them.
- Ctrl+Win isn't sent through the real hook in automated tests (Wispr Flow on the dev laptop would react). The hook
  plumbing is tested with the Menu key, Esc and Ctrl+Alt+X, and Ctrl+Win by the Matcher unit tests. The owner
  confirmed that Ctrl+Win dictation works in the installed app.
- English only. The cleanup can't bring back words the recognizer dropped, and it leaves real words that are wrong
  in context ("charted" for "chatting", "cloud" for "Claude").
- The console command (`sst dictate`) has no cleanup; the tray app does.
- The installer isn't code-signed, so SmartScreen warns on first run.

## Next steps

1. Merge in order: #2, #4, #6, #8, #10, then #12 (use "Create a merge commit"). Then tag `v0.1.0` on `main` and push
   the tag, and the Release workflow publishes the installer.
2. Owner:
   - install the phase 5 build
   - in Settings, choose a cleanup model, check the API key (Test), add the word list
   - choose the laptop mic
3. Roadmap:
   - **Phase 6, accuracy on the owner's voice:**
     - `sst bench`: a reading test with reference texts (about 30 sentences), scored for word errors
     - compare Parakeet, Parakeet + cleanup model 1 or 2, and a Whisper model if IT adds one to the gateway (vLLM
       serves Whisper; it would also cover Japanese/Tamil)
     - trim silence (VAD)
   - **Phase 7, text without the gateway:** rule-based fillers and spoken "new line" / "new paragraph" for when cleanup
     is off; self-corrections; spacing and capitals that fit the text before the cursor (UI Automation); snippets.
   - **Phase 8, command mode and context:** select text, hold the key and say "make this formal" (gateway model);
     per-app style (casual in Slack, formal in Outlook, plain in terminals and code editors).
   - **Phase 9, distribution:** auto-update from GitHub Releases, code signing (needs a certificate), a "paste last
     transcript" hotkey.
4. Waiting on the owner:
   - Japanese/Tamil needed?
   - the word list for the dictionary
   - the apps used most
   - code-signing budget
   - the product name (SST Dictation?)
   - ask IT: a local Whisper model on the gateway, and switching on `qwen3` / `sensenova-u1.5`
