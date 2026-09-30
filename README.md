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
- Rflow then opens its window. The first time, a welcome helps you choose your microphone (with a live level) and try
  your first dictation. Closing the window keeps Rflow running in the tray (the icon near the clock), so dictation
  keeps working; it also starts when you sign in. Open the window again from the Start menu or by clicking the tray
  icon; **right-click** the icon for the menu (Quit is there).
- **Updates:** when a new version is published, Rflow shows a banner in its window and a notification. **Update now**
  downloads it, checks it against its published SHA-256, installs it and restarts Rflow.
- Settings and history live in `%APPDATA%\sst`, recordings in `%LOCALAPPDATA%\sst\recordings`, reading tests in
  `%LOCALAPPDATA%\sst\bench`, logs in `%LOCALAPPDATA%\sst\logs`. Uninstall from *Settings → Apps*.
- `rflow-cli.exe` next to it is the command-line tool, e.g. `rflow-cli devices`, `rflow-cli file x.wav` or
  `rflow-cli eval` (score your reading tests).

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
afterwards. Dictated text is kept out of Windows clipboard history (Win+V).

**The window** has a sidebar:

| Page | What it's for |
|---|---|
| **Home** | how to dictate, your words this week and in total, words per minute, day streak, and your recent dictations by day, each with a copy button |
| **Dictionary** | *Your words*: names, products and terms the AI cleanup should spell your way |
| **Reading test** | how well Rflow understands your voice (below) |
| **AI cleanup** | the model that cleans up the text (below) |
| **Settings** | dictation key, microphone with a live level, beeps, keeping recordings, starting with Windows, updates |
| **Profiles** | one setup per person (below) |

It follows Windows' light or dark mode. Changes in Settings apply at once; AI cleanup has a Save button.

**AI cleanup (optional):**

1. Choose a **provider** and give it what it needs:

   | Provider | What to enter |
   |---|---|
   | OpenAI, Anthropic, Google Gemini, Groq | an **API key** ("Get a key" opens the provider's page) |
   | Ollama (on this computer) | nothing: it uses `http://localhost:11434/v1` (change it if yours runs elsewhere) |
   | vLLM or another OpenAI-compatible server | its **address** (e.g. `http://localhost:8000/v1`, LM Studio, a company AI gateway) and a key if it needs one |

2. **Load models**, choose a **model** and optionally a **backup model**, click **Test**, then **Save**. Fast chat
   models suit dictation (e.g. gpt-4o-mini, a Haiku model, a Flash-Lite model, llama-3.1-8b-instant); models that
   "think" first are usually too slow.
3. Add **your words** in the Dictionary (names, company, products, tech terms), so they come out spelled right.

Each provider gets the request it understands: Anthropic its own Messages API, OpenAI without the options only
self-hosted models need, and so on. Switching the provider back and forth keeps each one's key and address. Only the
finished text goes to the provider, never audio. If the model fails, the backup is used; if the provider is slow or
unreachable, the text is typed as heard at once and the pill says "Typed as heard". Home keeps both versions (hover a
dictation). Keys are stored in `gateway.json`, encrypted for your Windows account (DPAPI).

**Profiles:** several people on one computer, or a work and a private setup, each get a profile (the button under the
logo, or the *Profiles* page). Each profile has its own dictation key, microphone, words, AI provider and keys,
dictations, stats and reading tests; a new one starts with the welcome. The first profile keeps its files in
`%APPDATA%\sst`, the others in `%APPDATA%\sst\profiles\<name>`.

**Reading test:** the *Reading test* page measures how well Rflow understands *your* voice, microphone and words. There
are 5 sets of 30 short sentences; each test is one set (Record / Stop, or Space; about 6 minutes; you can leave and
continue later), and **New test** moves on to the next set. Sets A and B are for practice: the words Rflow suggests for
Your words come from them. Sets C to E are the real test, so a better score there isn't just learned by heart. **Score**
scores this test; **Score all tests** scores every test together, which gives a much surer answer. Rflow shows:

- the share of words it got wrong with speech recognition alone and with your cleanup model and backup model, with a 95%
  range, and whether a setup is really better than the first one or just lucky
- errors on names and terms apart, the time per sentence, and the words it misheard most
- a warning when a microphone sounds like a phone call (a Bluetooth headset while its microphone is on), clips or is very
  quiet

Tick the suggested words and click **Add to Your words**, then **Score again** to see the difference. Recordings and
results (`report.md`, `results.json`, and `session.json` with the set and microphone) stay in
`%LOCALAPPDATA%\sst\bench\<date>`, and results for all tests go to `bench\summary`. `rflow-cli eval` scores every test
again from the command line: `--model <name>` tries another cleanup model, `--no-cleanup` skips cleanup, and
`--degrade narrowband` (or `gain:-20`) shows what a worse microphone would do to the same recordings. How this feeds the
accuracy work is in [docs/accuracy.md](docs/accuracy.md).

- Using Wispr Flow too? Quit it first: it also listens to Ctrl+Win, and both would type. Rflow warns you if it's running.
- Nothing typed into one particular app? That app is probably running as administrator; Windows doesn't let normal
  programs type into those.

**From the source** (developers): `uv run sst app` starts the app (window and tray; quit the installed Rflow first:
only one can dictate); `dictate.cmd` / `uv run sst dictate` is the same
in a console window, without cleanup. Also `uv run sst web` (a Record button in the browser, served on 127.0.0.1 only),
`uv run sst start` (record in the terminal), `uv run sst file x.wav`, `uv run sst devices` and
`uv run sst eval [<folder>...]` (score reading tests).

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
├─ docs/accuracy.md           the accuracy plan: research summary, target pipeline, phases
├─ tests/                     pytest suite
├─ scripts/download_model.py  fetches models into models/
├─ scripts/build_installer.py PyInstaller -> add model -> smoke tests -> Inno Setup
├─ scripts/make_*.py          draw the window's small images, the installer's pictures, the website's screenshots
├─ packaging/                 installer recipe: sst_gui.py / sst_app.py (entry points), sst.spec, installer.iss,
│                             notices, images/ (the setup wizard's pictures)
├─ models/                    downloaded models (git-ignored)
├─ recordings/                your recordings + transcripts (git-ignored)
└─ sst/                       the Python package (the app's internal name)
   ├─ app.py                  the app: tray icon, recording pill, dictation, updates; "open Rflow again" (Qt)
   ├─ window.py               the window: Home, Dictionary, Reading test, AI cleanup, Settings, welcome; light/dark
   ├─ bench.py                the reading test's sets of sentences, test sessions, the fair word comparison
   ├─ evaluate.py             `sst eval`: replays the tests through setups; error rates, 95% ranges, microphones
   ├─ updates.py              in-app updates from GitHub Releases (checksum-verified)
   ├─ settings.py             profiles, settings, history, stats and "start with Windows" (%APPDATA%\sst)
   ├─ gateway.py              AI cleanup: the providers and their request formats, backup model, timeouts, keys (DPAPI)
   ├─ cli.py                  the `sst` command
   ├─ dictate.py              Dictation: hotkey events -> record -> transcribe -> clean up -> type
   ├─ hotkey.py               global hotkeys (low-level keyboard hook) and sending keys
   ├─ paste.py                paste text into the focused app, then restore the clipboard
   ├─ web.py                  local server for the web page (127.0.0.1 only)
   ├─ static/                 the Record / Stop page (index.html), the app icon (sst.ico), the window's images (ui/)
   ├─ audio.py                microphone recording, WAV read/write, measuring a recording, splitting long audio
   └─ engines/
      ├─ __init__.py          engine list + load_engine()
      └─ parakeet.py          Parakeet via sherpa-onnx (CPU); audio over 3 minutes is split at pauses
```

## Adding another engine

Create `sst/engines/<name>.py` with a class that has `name` and `transcribe(audio, sample_rate) -> str`,
then add it to `ENGINES` and `load_engine()` in `sst/engines/__init__.py`. Use it with `uv run sst --engine <name> start`.
