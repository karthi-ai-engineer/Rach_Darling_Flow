# Handoff

Where the project stands, so work can continue on any device. Updated at the end of every phase.
The product is **Rflow** (the repository and the Python package keep their names: Rach_Darling_Flow, `sst`).

_Last updated: 2026-09-30_

## Status

| Phase | What | State |
|---|---|---|
| 0 | Record and transcribe locally: CLI (`sst start`, `sst file`) and web page (`sst web`) | done, on `main` |
| 1 | Dictate into any app with a global hotkey (`sst dictate`) | done, on `main` (PR #2) |
| setup | GitHub CI (lint, tests, CodeQL), Dependabot, templates, CLAUDE.md, this file | done, on `main` (PR #4) |
| 2 | Windows installer (no Python needed), CI app build, release pipeline | done, on `main` (PR #6) |
| 3 | Wispr-style hotkeys: hold Ctrl+Win, Ctrl+Win+Space hands-free, optional Menu key | done, on `main` (PR #8) |
| 4 | A real app: tray icon, recording pill, settings, history, logs (Qt) | done, on `main` (PR #10) |
| 5 | AI text cleanup (model chosen in Settings), key encrypted; one-piece transcription up to 3 min | done, on `main` (PR #12) |
| 6 | **Rflow 1.0**: product name, generic endpoint / key / model, in-app updates, download website | done, on `main` (PR #14), released **v1.0.0** |
| fix | Update checks retry when the first connection stalls | done, on `main` (PR #16), released **v1.0.1** |
| 7 | **Reading test**: accuracy on the user's own voice, per cleanup model; misheard words → Your words | done, on `main` (PR #18), released **v1.1.0** |
| 8 | **The Rflow window**: a complete app like Wispr Flow (Home, Dictionary, Reading test, AI cleanup, Settings), first-run welcome, branded installer; 1.2.0 | PR #20, waiting for the owner's test + merge |
| 9 | **AI providers and profiles**: OpenAI, Anthropic, Gemini, Groq, Ollama, vLLM; one setup per person; 1.3.0 | PR #22 (stacked on #20), waiting for the owner's test + merge |

Released: v1.0.0, v1.0.1 and v1.1.0 (GitHub Releases). Website: https://rachdarlingflow-site.vercel.app (Vercel,
`site/`). The in-app update path is verified end to end: the owner's installed 1.0.0 showed the banner and updated
itself to 1.0.1.

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
uv run sst app                                     # the tray app
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
- The analysis scripts were ad hoc. Phase 7 replaced them with the reading test (see below).

## Company AI gateway (tested 2026-09-30)

- The owner's company runs an internal, OpenAI-compatible AI gateway (models, chat/completions, responses,
  completions, embeddings, images/generations, audio/transcriptions). Its address is in the owner's own settings
  (`%APPDATA%\sst\gateway.json`), deliberately not in this public repository.
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
- The test scripts were ad hoc (httpx, one model per run). The reading test (phase 7) now compares the cleanup models
  on the owner's own recordings, one model at a time.

## Text cleanup (phase 5)

- (Phase 6 made this generic: see below. The owner uses these two models as model and backup.)
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

## Rflow 1.0 (phase 6)

- **Name.** Rflow is the tray app `Rflow.exe`, and the command-line tool is `rflow-cli.exe` (Windows ignores case, so
  the two names must differ). The installer is `Rflow-Setup-<ver>.exe`. The installer keeps the same AppId, so SST
  Dictation installs upgrade in place: their folder and their Settings > Apps entry stay. `[InstallDelete]` removes the
  old `SST Dictation.exe` / `sst.exe` and the old shortcuts, and `[Registry]` deletes the old "SST Dictation" Run value.
  The mutexes keep their old names (`SST-Dictation-running`, `SST-Dictation-dictate`), so new installers still detect a
  running old version. Data folders stay `%APPDATA%\sst` and `%LOCALAPPDATA%\sst`.
- **Generic cleanup.** The code has no company defaults. Settings > "Text cleanup with an AI model" has:
  - an on/off switch
  - Endpoint and API key (optional, e.g. for Ollama; no empty Bearer header is sent)
  - "Load models" (`Polisher.models()`: GET /models; models marked `is_cloud: false` are listed first)
  - Model and Backup model (editable combos: pick one or type a name), and Test

  `Settings.cleanup` / `cleanup_model` / `cleanup_fallback`: an old settings file that has a model but no `cleanup`
  key loads with cleanup on. The owner's backup model has to be picked once in Settings, since the fixed pair of models
  is gone.
- **In-app updates** (`sst/updates.py`):
  - The installed app checks `api.github.com/.../releases/latest` 20 s after start and then every 6 h; the tray menu
    has "Check for updates". A newer tag with the assets `Rflow-Setup.exe` and `Rflow-Setup.exe.sha256` shows a blue
    banner in the window (What's new / Update now), a tray notification (clicking it opens the window), and a menu item.
  - The installer is downloaded to `%TEMP%\Rflow-update`. Only GitHub hosts are accepted after redirects, and the
    SHA-256 must match, or the file is deleted.
  - Then the app releases its `SST-Dictation-running` mutex (otherwise setup stops at AppMutex) and starts setup with
    `/SILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE=1`, then quits. setup's `[Run]` entry with `Check: IsInAppUpdate`
    starts the new Rflow.
  - A source checkout doesn't check for updates; "Update now" there only opens the release page.
- **Release workflow:** it copies the build to `dist/release/Rflow-Setup.exe`, writes `Rflow-Setup.exe.sha256`
  (sha256sum), and publishes both to the GitHub Release "Rflow vX.Y.Z" (the tag must equal `sst.__version__`).
  `releases/latest/download/Rflow-Setup.exe` is the stable download link.
- **Website:** `site/index.html` is a single static page with screenshots in `site/img` (rendered from the real
  widgets, generic example settings) and `favicon.ico`. It works in dark and light mode, and at phone width (checked:
  no horizontal overflow at 390 px). The download button links to the stable link, and a script shows the latest
  version, size and date from the GitHub API. Vercel: import the repo with **Root Directory `site`**, Framework
  "Other", and no build command.
- **Public repo:** the company gateway's hostname and IP were removed from the code and the docs. Older commits in the
  git history still contain the hostname; removing it would mean rewriting published history. That is the owner's
  decision, and so is whether the repo stays public.
- The Settings screenshot exposed a layout bug: a long word-wrapped QLabel inside a QFormLayout overlapped and got
  clipped. The text is now one short line, with the details in a tooltip.

## Reading test (phase 7)

- Tray menu → "Reading test..." opens `ReadingTest` (`sst/app.py`). It shows one of the 30 sentences in `sst/bench.py`
  (everyday dictation with the owner's kind of words: GitHub, pull request, merge commit, branch, Rflow, Parakeet,
  Vercel, Tamil, Japanese, Teams, CodeQL, Karthi, numbers). It has Record / Stop (Space too), Redo, Back / Next and a
  live level. It records with the microphone chosen in Settings, and rejects recordings under 0.5 s.
- Each recording is saved as `NN.wav` + `NN.txt` (the sentence) in `%LOCALAPPDATA%\sst\bench\<date_time>`. Closing the
  window halfway loses nothing: the next "Reading test..." continues the newest folder while it has sentences left
  (`bench.unfinished()`).
- **Score** (`bench.score()`, on a thread):
  - Transcribes every recording with Parakeet first, then cleans up all the texts with the cleanup model, then with the
    backup model, one request at a time and with no fallback, so each model is measured on its own.
  - Word error rate = (substituted + missing + extra words) / words in the sentence. `bench.words()` makes the
    comparison fair: case, punctuation and hyphens don't count, "70" equals "seventy", ok = okay.
  - The alignment is Levenshtein; among equally short alignments it pairs words that look alike, so "Tamil → Tamar" is
    listed, not "Tamil → please".
  - Writes `results.json` and `report.md` next to the recordings.
- The results page shows a table per setup (errors, wrong / total, seconds per sentence; the best in bold) and the most
  misheard words. Suggested words (misheard, not common words, spelled as in the sentence) can be ticked and added to
  Your words, which activates them at once, and then **Score again** shows the difference.
- `sst bench <folder> [--model <name>]...` (`rflow-cli bench` when installed) re-scores a folder from the command line,
  by default with the models in Settings.
- `ParakeetEngine.transcribe` has a lock now: dictating while the test scores must not use one recognizer from two
  threads.
- The installed app deletes `%TEMP%\Rflow-update` (the downloaded installer, 500 MB) 60 s after it starts.
- A dry run on the model's test WAV against the gateway (both models, one at a time): 0% errors for every setup;
  Qwen3.8-27B 2.75 s (cold), Qwen3-30B 0.90 s per sentence. The owner's real run is still to come.
- Not done in this phase: trimming silence (VAD) and a Whisper comparison. The owner's reading test results should
  decide whether they're worth it.

## The Rflow window (phase 8)

- The owner's request: Rflow should open as a complete application, like Wispr Flow, so that people who download it
  from the website or a link can set it up and change everything inside the app.
- `sst/window.py`, `MainWindow`: a sidebar (Home, Dictionary, Reading test, AI cleanup, Settings; the status and the
  version at the bottom) and a page stack. It is native Qt with one stylesheet (`stylesheet(theme)`, the website's
  colours) that follows Windows' light/dark mode (`styleHints().colorSchemeChanged`). An embedded browser
  (QtWebEngine) would have added ~150 MB and a slower start for the same look. The icons come from Windows' own icon
  font (Segoe Fluent Icons, or MDL2 Assets on Windows 10). The checkbox tick and the dropdown arrows are small PNGs in
  `sst/static/ui`, drawn by `scripts/make_ui_images.py`, because a Qt stylesheet can only show images from files.
- The window keeps no state. It calls the app: `TrayApp` in `sst/app.py` (`apply_settings`, `save_cleanup`,
  `add_words`, `remove_word`, `score_reading`, `finish_welcome`, `window_closed`, updates), or `PreviewApp` in the
  self-test, the tests and `scripts/make_site_screenshots.py`. Settings apply at once; AI cleanup has a Save button,
  because a half-typed endpoint or key shouldn't be used.
- **Home:**
  - a greeting and how to dictate with the chosen key
  - the stats: words this week and in total, words per minute, day streak. `Stats` in `sst/settings.py`, file
    `%APPDATA%\sst\stats.json`, updated on every dictation; `Dictation.on_result` now also gives the recording's
    length. The first time, it starts from the history, which has no lengths, so words per minute is shown after half
    a minute of new dictation.
  - the recent dictations grouped by day, with copy buttons
- **Dictionary:** Your words, with add (comma-separated) and remove. It says so when AI cleanup is off.
- **Reading test:** the phase 7 test, now a page (`ReadingTestPage`), with a "New test" button. Leaving the page stops a
  recording. Space records only while the test has the focus (`WidgetWithChildrenShortcut`).
- **Settings:** key, microphone with a live level (`MicrophoneBox` + `audio.LevelMeter`), beeps, keep recordings, start
  with Windows, check for updates, logs, website, report a problem.
- **Welcome** (first run, `Settings.welcomed` false): step 1 the microphone with its level; step 2 try a dictation in a
  text box (the real dictation pastes into it); step 3 optional AI cleanup. Existing users see it once too, since
  their settings file has no `welcomed` yet.
- **Opening Rflow again** (Start menu, desktop) shows the running window: the running app listens on the local pipe
  `Rflow-window-<user>` (`QLocalServer`), and a second start (`show_running_window()`) asks it to open and exits.
  `--startup` (sign-in) starts with only the tray icon. Closing the window hides it; the first time, a notification
  says that Rflow keeps running (`Settings.told_about_tray`).
- **Audio fix:** `Recorder.start()` re-reads the device list by restarting PortAudio, which closes every open stream. With
  a level meter open, a dictation starting would have pulled the rug from under it (and the reading test's recorder
  had the same risk). `audio._open_streams` now counts open streams, and the list is only re-read when none is open.
- **Installer:** Rflow-branded wizard pictures in `packaging/images` (`scripts/make_installer_images.py`, BMP at
  100–200% scaling), the welcome page switched on, and texts that say what Rflow does and that it opens after Finish.
  Only options every Inno Setup 6 knows are used: the CI build machine's copy is preinstalled, and its version isn't
  pinned.
- **Website:** `site/img/app.png` (Home), `settings.png` (AI cleanup) and the new `welcome.png` are rendered from the
  real window by `scripts/make_site_screenshots.py`, with generic example data. There is a new section, "A real app,
  not just a tray icon".
- Tests: 156. `tests/test_window.py` builds every page with `PreviewApp`. `tests/test_app.py` runs the real `TrayApp`
  with the settings in memory, a fake model and a fake keyboard hook: loading, a dictation shown on Home, a key
  change applied at once, words added, close to tray, and a second start showing the window.
- Checked visually: every page rendered with Windows fonts, light and dark, at 1000×700 and at the minimum 780×540. That
  caught history cards drawn over the page (removed widgets are only deleted later, so `clear()` hides them first),
  checkboxes without a box, and native-looking dropdown arrows.

## AI providers and profiles (phase 9)

- The owner's request: people use different AI services (OpenAI, Anthropic, Gemini, Groq, Ollama, vLLM...), so the AI
  cleanup offers them by name. Also profiles (e.g. Karthi, Rahul), each person with their own setup.
- **Providers** (`sst/gateway.py`, `PROVIDERS`): OpenAI, Anthropic, Google Gemini, Groq, Ollama (on this computer), and
  vLLM or another OpenAI-compatible server. Each entry says its usual address, which API it speaks, whether it needs a
  key, whether it's the user's own server (then the address can be edited), where to get a key, and a model hint. Each
  provider gets the request it understands (`Polisher._body`):
  - **Anthropic:** `/messages` with `x-api-key` and `anthropic-version`, the system prompt as `system`, and the answer
    from the `content` blocks. Its model list is `/models?limit=1000`.
  - **OpenAI:** `max_completion_tokens`, since its newest models refuse `max_tokens`. Reasoning models (o-series,
    gpt-5) get no temperature and room to think; they are usually too slow for dictation anyway. No
    `chat_template_kwargs`: OpenAI refuses fields it doesn't know.
  - **Gemini** (its OpenAI-compatible endpoint): no token limit, because its "thinking" counts against it and would
    cut the answer short. Its model ids lose their `models/` prefix.
  - **Groq:** the plain OpenAI format.
  - **Ollama and vLLM:** also `chat_template_kwargs: {enable_thinking: false}` (Qwen3 on the company gateway).
  - Error messages are read from `error.message` for every provider.
  - "Load models" leaves out models that don't write text (whisper, tts, embeddings, images, moderation...).
- **GatewayConfig** has `provider` and `others` (the other providers' address and key, encrypted too), so switching
  back and forth loses nothing. An old file with only an address gets its provider from the address
  (`provider_for`): the owner's company gateway becomes "vLLM or another OpenAI-compatible server", with the same
  address, key and model. That was checked against the real files, and a real request through the new code worked
  (Qwen3.8-27B, 2.3 s).
- The **AI cleanup page** has a provider dropdown. The address row appears only for Ollama and vLLM, and "Get a key"
  only for the cloud providers. A new user starts with OpenAI selected.
- **Profiles** (`sst/settings.py`, `Profiles` in `%APPDATA%\sst\profiles.json`): each profile has its own
  `settings.json` (key, microphone, words, cleanup model, welcome...), `gateway.json` (provider and keys),
  `history.jsonl`, `stats.json` and reading tests. The first profile (`default`) keeps the files where they were
  before profiles: nothing moves, and an older Rflow still reads them. The others live in
  `%APPDATA%\sst\profiles\<id>` and `%LOCALAPPDATA%\sst\bench\profiles\<id>`. `bench.unfinished()` only counts
  date-named test folders, so the `profiles` folder doesn't confuse it.
- **In the window:** a profile button under the logo (a menu to switch, "New profile...", "Manage profiles") and a
  *Profiles* page (rename in place, switch, delete with a question, create). The Home greeting uses the name, and the
  welcome asks for it. Switching (`TrayApp._activate_profile`) loads the other files, applies the key, microphone
  and cleanup at once, and builds the window again, since every page shows the profile's own data. The profile in
  use and the first profile can't be deleted.
- `sst bench` uses the profile in use.
- Tests: 191. Every provider's request format against the local fake, Anthropic's errors and models, the model filter,
  `provider_for`, the encrypted `others`. Profiles: files, ids, round trip, removal, damaged files. The window: each
  provider's fields, switching providers keeping keys, the profile button, the greeting, the Profiles page, the
  welcome's name. The real TrayApp: creating Rahul (welcome, empty words, own key) and switching back to the first
  profile's words and key.
- **Not tried for real:** OpenAI, Anthropic, Gemini and Groq with real keys (only against the fakes, which check the
  request format). The owner, or anyone with a key, should press Test once for each.

## Known limitations

- Apps running as administrator don't receive the text, because Windows blocks input from normal programs into them.
- Ctrl+Win isn't sent through the real hook in automated tests (Wispr Flow on the dev laptop would react). The hook
  plumbing is tested with the Menu key, Esc and Ctrl+Alt+X, and Ctrl+Win by the Matcher unit tests. The owner
  confirmed that Ctrl+Win dictation works in the installed app.
- English only. The cleanup can't bring back words the recognizer dropped, and it leaves real words that are wrong
  in context ("charted" for "chatting", "cloud" for "Claude").
- The console command (`sst dictate`) has no cleanup; the tray app does.
- The installer isn't code-signed, so SmartScreen warns on the first install. In-app updates don't trigger it, because
  a file downloaded by the app isn't marked as coming from the internet.
- The Python client's first connection to the company gateway, and sometimes to GitHub, stalls on the dev laptop. Both
  clients use short connect timeouts with retries (`sst/gateway.py`, `sst/updates.py`).

## Next steps

1. Owner: try PR #20. Quit Rflow (tray → Quit), install `dist\Rflow-Setup-1.2.0.exe` over it, and look at every page.
   Also check: opening Rflow from the Start menu while it runs brings the window up, and closing the window keeps
   dictation working.
2. Merge PR #20, then tag `v1.2.0` on `main` and push the tag. Installed copies are offered the update.
3. Owner: try PR #22 (`dist\Rflow-Setup-1.3.0.exe`). In AI cleanup the company gateway shows as "vLLM or another
   OpenAI-compatible server"; press Test. With a key for any cloud provider, try it too. Make a second profile, switch
   back and forth, and delete it. Then merge PR #22 (after #20: retarget it to `main` first) and tag `v1.3.0`.
4. Roadmap:
   - **Phase 10, text without the AI endpoint:** rule-based fillers and spoken "new line" / "new paragraph"; spacing
     and capitals that fit the text before the cursor (UI Automation); snippets.
   - **Phase 11, command mode and context:** "make this formal" on selected text; per-app style.
   - **Accuracy, if the reading test calls for it:** trim silence (VAD); a Whisper model if IT adds one to the gateway.
   - **Later:** code signing (removes the SmartScreen warning), and a "paste last transcript" hotkey.
5. Waiting on the owner:
   - Japanese/Tamil needed?
   - the word list
   - the apps used most
   - code-signing budget
   - the git history question above
