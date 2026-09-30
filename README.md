# Rflow

[![CI](https://github.com/karthi-ai-engineer/Rach_Darling_Flow/actions/workflows/ci.yml/badge.svg)](https://github.com/karthi-ai-engineer/Rach_Darling_Flow/actions/workflows/ci.yml)

**Speak anywhere, Rflow types it.** Hold **Ctrl+Win** in any Windows app, speak, let go: your words are typed where
your cursor is. Speech is recognised **on your laptop** by NVIDIA Parakeet (English); your voice is never uploaded.
Optionally, an AI model you choose cleans up the text (punctuation, fillers, your own words).

## Install

Download **`Rflow-Setup.exe`** (the latest version:
[releases/latest](https://github.com/karthi-ai-engineer/Rach_Darling_Flow/releases/latest)) and run it. It needs no
administrator rights, no Python and no internet connection: the ~650 MB speech model is inside. Windows 10/11, 64-bit.

- Windows may say *"Windows protected your PC"*, because the installer isn't code-signed yet. Click **More info → Run anyway**.
- Rflow then runs quietly in the tray (the icon near the clock) and starts when you sign in. **Click** the icon for its
  window (history, the Settings button, update banner); **right-click** it for the menu (Settings, Check for updates, Quit).
- **Updates:** when a new version is published, Rflow shows a banner in its window and a notification. **Update now**
  downloads it, checks it against its published SHA-256, installs it and restarts Rflow.
- Settings and history live in `%APPDATA%\sst`, recordings in `%LOCALAPPDATA%\sst\recordings`, logs in
  `%LOCALAPPDATA%\sst\logs`. Uninstall from *Settings → Apps*.
- `rflow-cli.exe` next to it is the command-line tool, e.g. `rflow-cli devices` or `rflow-cli file x.wav`.

## Use

Click in any text box (Notepad, Chrome, Slack, Teams, VS Code...) and:

| Keys | What happens |
|---|---|
| hold **Ctrl+Win** while speaking | push-to-talk: types the text when you let go |
| tap **Ctrl+Win**, speak, tap again | hands-free: records until the second tap, then types the text |
| **Ctrl+Win+Space**, speak, Ctrl+Win | hands-free too |
| **Esc** while recording | cancels; nothing is typed |

Windows' own Ctrl+Win shortcuts still work: Ctrl+Win+D (new desktop), Ctrl+Win+←/→ and so on simply drop the recording.
Releasing Win doesn't open the Start menu. The key can be changed in Settings (e.g. the Menu key).

While you speak, a small pill near the bottom of the screen shows a live level; then dots while the text is
transcribed, and "Typed" when it's done. It never takes the keyboard focus. A recording stops by itself after 3 minutes
and is typed as usual. The text is pasted where your cursor is, and whatever you had copied is put back on the clipboard
afterwards. Dictated text is kept out of Windows clipboard history (Win+V); Rflow's own History keeps the last 200.

**Text cleanup (optional):** Settings → *Text cleanup with an AI model*:

1. Enter an **endpoint** (any OpenAI-compatible API: `https://api.openai.com/v1`, a local Ollama or LM Studio such as
   `http://localhost:11434/v1`, a company AI gateway...) and, if it needs one, an **API key**.
2. **Load models**, choose a **model** and optionally a **backup model**, and click **Test**.
3. Add **your words** (names, company, products, tech terms), so they come out spelled right.

Only the finished text goes to the endpoint, never audio. If the model fails, the backup is used; if the endpoint is
slow or unreachable, the text is typed as heard at once and the pill says "Typed as heard". History keeps both versions
(hover a line). The key is stored in `%APPDATA%\sst\gateway.json`, encrypted for your Windows account (DPAPI).

- Using Wispr Flow too? Quit it first: it also listens to Ctrl+Win, and both would type. Rflow warns you if it's running.
- Nothing typed into one particular app? That app is probably running as administrator; Windows doesn't let normal
  programs type into those.

**From the source** (developers): `uv run sst app` starts the tray app; `dictate.cmd` / `uv run sst dictate` is the same
in a console window, without cleanup. Also `uv run sst web` (a Record button in the browser, served on 127.0.0.1 only),
`uv run sst start` (record in the terminal), `uv run sst file x.wav` and `uv run sst devices`.

## Setup (from source)

Needs Windows 10/11 and [uv](https://docs.astral.sh/uv/).

```
git clone https://github.com/karthi-ai-engineer/Rach_Darling_Flow.git
cd Rach_Darling_Flow
uv sync                                          # install dependencies into .venv
uv run python scripts/download_model.py parakeet # ~630 MB model into models/
```

## Development

```
uv run pytest           # tests (they use fakes: no keys pressed, no microphone, model or network needed)
uv run ruff check .     # lint
```

**Build the installer** with **`build_installer.cmd`** (needs the model and
[Inno Setup 6](https://jrsoftware.org/isinfo.php): `winget install JRSoftware.InnoSetup`). It builds `Rflow.exe` and
`rflow-cli.exe` with PyInstaller, checks that they really transcribe and open their windows, and writes
`dist\Rflow-Setup-<version>.exe`.

**Release** (this is what users' Update button picks up):

1. Raise `__version__` in `sst/__init__.py` (e.g. `1.1.0`) in the phase's pull request, and merge it.
2. Tag `main` with `v1.1.0` and push the tag.
3. The Release workflow builds the installer and publishes a GitHub Release with `Rflow-Setup.exe` and
   `Rflow-Setup.exe.sha256`. The website's download button and every installed Rflow see it right away.

**Website:** `site/` is a static page for Vercel (Root Directory `site`, no build step). Its download button links to
`releases/latest/download/Rflow-Setup.exe`, so it never needs changing for a new version.

Work happens phase by phase: an issue, a branch and a pull request into `main`, checked by CI.
`CLAUDE.md` has the working rules, and `HANDOFF.md` says where things stand and what comes next.

## Layout

```
Rach_Darling_Flow/
├─ site/                      the download website (Vercel): index.html, screenshots, icon
├─ dictate.cmd                double-click: dictate in a console window (from source)
├─ web.cmd                    double-click: web page
├─ start.cmd                  double-click: terminal version
├─ build_installer.cmd        double-click: build dist\Rflow-Setup-<version>.exe
├─ CLAUDE.md                  working rules (branches, PRs, authorship)
├─ HANDOFF.md                 current state and next steps, to resume on any device
├─ .github/                   CI, CodeQL, Release, Dependabot, issue and PR templates
├─ tests/                     pytest suite
├─ scripts/download_model.py  fetches models into models/
├─ scripts/build_installer.py PyInstaller -> add model -> smoke tests -> Inno Setup
├─ packaging/                 installer recipe: sst_gui.py / sst_app.py (entry points), sst.spec, installer.iss, notices
├─ models/                    downloaded models (git-ignored)
├─ recordings/                your recordings + transcripts (git-ignored)
└─ sst/                       the Python package (the app's internal name)
   ├─ app.py                  the tray app: tray icon, recording pill, settings, history, update banner (Qt)
   ├─ updates.py              in-app updates from GitHub Releases (checksum-verified)
   ├─ settings.py             settings, history and "start with Windows" (%APPDATA%\sst)
   ├─ gateway.py              text cleanup with a model on any OpenAI-compatible endpoint: backup model, timeouts
   ├─ cli.py                  the `sst` command
   ├─ dictate.py              Dictation: hotkey events -> record -> transcribe -> clean up -> type
   ├─ hotkey.py               global hotkeys (low-level keyboard hook) and sending keys
   ├─ paste.py                paste text into the focused app, then restore the clipboard
   ├─ web.py                  local server for the web page (127.0.0.1 only)
   ├─ static/                 the Record / Stop page (index.html) and the app icon (sst.ico)
   ├─ audio.py                microphone recording, WAV read/write, saving recordings, splitting long audio
   └─ engines/
      ├─ __init__.py          engine list + load_engine()
      └─ parakeet.py          Parakeet via sherpa-onnx (CPU); audio over 3 minutes is split at pauses
```

## Adding another engine

Create `sst/engines/<name>.py` with a class that has `name` and `transcribe(audio, sample_rate) -> str`,
then add it to `ENGINES` and `load_engine()` in `sst/engines/__init__.py`. Use it with `uv run sst --engine <name> start`.
