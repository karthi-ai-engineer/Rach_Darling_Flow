# sst

[![CI](https://github.com/karthi-ai-engineer/Rach_Darling_Flow/actions/workflows/ci.yml/badge.svg)](https://github.com/karthi-ai-engineer/Rach_Darling_Flow/actions/workflows/ci.yml)

Record from the microphone and turn speech into text **locally** (nothing leaves the laptop).
The first engine is **NVIDIA Parakeet (English)**. More engines, such as Whisper or the office gateway, can be added later.

## Install on any laptop

Download **`SST-Dictation-Setup-<version>.exe`** from [Releases](https://github.com/karthi-ai-engineer/Rach_Darling_Flow/releases)
and run it. It doesn't need administrator rights, Python or an internet connection (the ~650 MB speech model is inside).
Windows 10/11, 64-bit.

- Windows may say *"Windows protected your PC"*, because the installer isn't code-signed. Click **More info → Run anyway**.
- **SST Dictation** then runs quietly in the tray (the icon near the clock) and starts when you sign in; there is no window
  to keep open. Right-click the icon for **History**, **Settings** (key, microphone, sounds, start with Windows) and **Quit**.
- Settings and history live in `%APPDATA%\sst`, recordings in `%LOCALAPPDATA%\sst\recordings`, logs in
  `%LOCALAPPDATA%\sst\logs`. Uninstall from *Settings → Apps*.
- `sst.exe` next to it is the command-line tool, e.g. `sst.exe devices` or `sst.exe file x.wav`.

## Use

**Dictate into any app:** start **SST Dictation** (the installed app, or `uv run sst app` from the source).
Then click in any text box (Notepad, Chrome, Slack, VS Code...) and:

| Keys | What happens |
|---|---|
| hold **Ctrl+Win** while speaking | push-to-talk: types the text when you let go (the same keys as Wispr Flow) |
| tap **Ctrl+Win**, speak, tap again | hands-free: records until the second tap, then types the text |
| **Ctrl+Win+Space**, speak, Ctrl+Win | hands-free too, as in Wispr Flow |
| **Esc** while recording | cancels; nothing is typed |

Windows' own Ctrl+Win shortcuts still work: Ctrl+Win+D (new desktop), Ctrl+Win+←/→ and so on simply drop the recording.
Releasing Win doesn't open the Start menu.

While you speak, a small pill near the bottom of the screen shows a live level; then it shows dots while the text is
transcribed, and "Typed" when it's done. It never takes the keyboard focus. There's also a beep at the start and end
(switch it off in Settings). A recording stops by itself after 3 minutes and is typed as usual.
The text is pasted where your cursor is, and whatever you had copied is put back on the clipboard afterwards.
Dictated text is kept out of Windows clipboard history (Win+V); SST's own History keeps the last 200.

`dictate.cmd` / `uv run sst dictate` is the same without the tray app, in a console window.

- **Quit Wispr Flow first** (tray icon → Quit). It listens to Ctrl+Win too, and both would type. sst warns you if it's running.
- Other keys: `--hotkey menu` uses the Menu key (≣, next to right Alt; its right-click menu is blocked), or a combination
  like `--hotkey ctrl+alt+d` (letters, digits, F1–F24, space, `muhenkan`/`henkan`...).
  If PowerToys remaps your Menu or Copilot key to Ctrl+Win (+Space), those keys work with the default as they are.
- Nothing gets typed into one particular app? That app is probably running as administrator, and Windows does not let normal
  programs type into those. Start `dictate.cmd` as administrator too.
- Add `--no-save` if you don't want the recordings kept in `recordings/`.

**Web page:** double-click **`web.cmd`** (or `uv run sst web`). Your browser opens a page with a Record button:
press it, speak, press it again (or use the Space bar), and the text appears below with a Copy button.
The server only listens on this laptop (`127.0.0.1:8765`). Close the black window to stop it.

**Terminal:**

```
uv run sst start        # records immediately; press Enter to stop, the text is printed
uv run sst file x.wav   # transcribe an existing 16-bit WAV file
uv run sst devices      # list microphones (* = default); pick one with --device N
```

Or double-click **`start.cmd`** for the terminal version.

Each recording is saved to `recordings/` as a `.wav` and `.txt` pair, so you can re-run the same audio through another engine later with `sst file`.

## Setup

Needs Windows 10/11 and [uv](https://docs.astral.sh/uv/).

```
git clone https://github.com/karthi-ai-engineer/Rach_Darling_Flow.git
cd Rach_Darling_Flow
uv sync                                          # install dependencies into .venv
uv run python scripts/download_model.py parakeet # ~630 MB model into models/
```

## Development

```
uv run pytest           # tests (they use fakes: no keys pressed, no microphone or model needed)
uv run ruff check .     # lint
```

**Build the installer** yourself with **`build_installer.cmd`** (it needs the model downloaded and
[Inno Setup 6](https://jrsoftware.org/isinfo.php): `winget install JRSoftware.InnoSetup`). It builds the app with PyInstaller,
checks that the built `sst.exe` really transcribes, and writes `dist\SST-Dictation-Setup-<version>.exe`.

**Release:** raise `__version__` in `sst/__init__.py`, merge, then tag `main` with `vX.Y.Z` and push the tag. The Release
workflow builds the installer and publishes it on GitHub. It can also be started by hand from the Actions tab to get a
test installer from any branch.

Work happens phase by phase: an issue, a branch and a pull request into `main`, checked by CI.
`CLAUDE.md` has the working rules, and `HANDOFF.md` says where things stand and what comes next.

## Layout

```
sst/
├─ dictate.cmd                double-click: dictate into any app with Ctrl+Win
├─ web.cmd                    double-click: web page
├─ start.cmd                  double-click: terminal version
├─ build_installer.cmd        double-click: build dist\SST-Dictation-Setup-<version>.exe
├─ CLAUDE.md                  working rules (branches, PRs, authorship)
├─ HANDOFF.md                 current state and next steps, to resume on any device
├─ .github/                   CI, CodeQL, Dependabot, issue and PR templates
├─ tests/                     pytest suite
├─ scripts/download_model.py  fetches models into models/
├─ scripts/build_installer.py PyInstaller -> add model -> smoke test -> Inno Setup
├─ packaging/                 installer recipe: sst_gui.py / sst_app.py (entry points), sst.spec, installer.iss, notices
├─ models/                    downloaded models (git-ignored)
├─ recordings/                your recordings + transcripts (git-ignored)
└─ sst/
   ├─ app.py                  the tray app: tray icon, recording pill, settings and history windows (Qt)
   ├─ settings.py             settings, history and "start with Windows" (%APPDATA%\sst)
   ├─ cli.py                  the `sst` command
   ├─ dictate.py              Dictation: hotkey events -> record -> transcribe -> type (shared by app and console)
   ├─ hotkey.py               global hotkeys (low-level keyboard hook) and sending keys
   ├─ paste.py                paste text into the focused app, then restore the clipboard
   ├─ web.py                  local server for the web page (127.0.0.1 only)
   ├─ static/                 the Record / Stop page (index.html) and the app icon (sst.ico)
   ├─ audio.py                microphone recording, WAV read/write, saving recordings, splitting long audio
   └─ engines/
      ├─ __init__.py          engine list + load_engine()
      └─ parakeet.py          Parakeet via sherpa-onnx (CPU); audio over 30 s is split at pauses
```

## Adding another engine

Create `sst/engines/<name>.py` with a class that has `name` and `transcribe(audio, sample_rate) -> str`,
then add it to `ENGINES` and `load_engine()` in `sst/engines/__init__.py`. Use it with `uv run sst --engine <name> start`.
