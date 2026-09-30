# sst

[![CI](https://github.com/karthi-ai-engineer/Rach_Darling_Flow/actions/workflows/ci.yml/badge.svg)](https://github.com/karthi-ai-engineer/Rach_Darling_Flow/actions/workflows/ci.yml)

Record from the microphone and turn speech into text **locally** (nothing leaves the laptop).
The first engine is **NVIDIA Parakeet (English)**. More engines, such as Whisper or the office gateway, can be added later.

## Use

**Dictate into any app:** double-click **`dictate.cmd`** (or `uv run sst dictate`) and wait for *Ready*.
Then click in any text box (Notepad, Chrome, Slack, VS Code...) and:

| Keys | What happens |
|---|---|
| tap **Ctrl+Alt+D**, speak, tap again | hands-free: records until the second tap, then types the text |
| hold **Ctrl+Alt+D** while speaking | push-to-talk: types the text when you let go |
| **Esc** while recording | cancels; nothing is typed |

A high beep means recording started, a lower beep means it stopped. A recording stops by itself after 3 minutes and is typed as usual.
The text is pasted where your cursor is, and whatever you had copied is put back on the clipboard afterwards.
Dictated text is kept out of Windows clipboard history (Win+V). Keep the black window open; you can minimise it.

- Hotkey already taken by another app? Use `uv run sst dictate --hotkey ctrl+alt+x` (letters, digits, F1–F24, space...).
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

Work happens phase by phase: an issue, a branch and a pull request into `main`, checked by CI.
`CLAUDE.md` has the working rules, and `HANDOFF.md` says where things stand and what comes next.

## Layout

```
sst/
├─ dictate.cmd                double-click: dictate into any app with Ctrl+Alt+D
├─ web.cmd                    double-click: web page
├─ start.cmd                  double-click: terminal version
├─ CLAUDE.md                  working rules (branches, PRs, authorship)
├─ HANDOFF.md                 current state and next steps, to resume on any device
├─ .github/                   CI, CodeQL, Dependabot, issue and PR templates
├─ tests/                     pytest suite
├─ scripts/download_model.py  fetches models into models/
├─ models/                    downloaded models (git-ignored)
├─ recordings/                your recordings + transcripts (git-ignored)
└─ sst/
   ├─ cli.py                  the `sst` command
   ├─ dictate.py              hotkey -> record -> transcribe -> paste loop (Windows)
   ├─ hotkey.py               global hotkeys (Windows RegisterHotKey)
   ├─ paste.py                paste text into the focused app, then restore the clipboard
   ├─ web.py                  local server for the web page (127.0.0.1 only)
   ├─ static/index.html       the Record / Stop page
   ├─ audio.py                microphone recording, WAV read/write, saving recordings, splitting long audio
   └─ engines/
      ├─ __init__.py          engine list + load_engine()
      └─ parakeet.py          Parakeet via sherpa-onnx (CPU); audio over 30 s is split at pauses
```

## Adding another engine

Create `sst/engines/<name>.py` with a class that has `name` and `transcribe(audio, sample_rate) -> str`,
then add it to `ENGINES` and `load_engine()` in `sst/engines/__init__.py`. Use it with `uv run sst --engine <name> start`.
