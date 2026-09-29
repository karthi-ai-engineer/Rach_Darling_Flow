# sst

Record from the microphone and turn speech into text **locally** (nothing leaves the laptop).
The first engine is **NVIDIA Parakeet (English)**. More engines, such as Whisper or the office gateway, can be added later.

## Use

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

## Setup (already done on this laptop)

```
uv sync                                          # install dependencies into .venv
uv run python scripts/download_model.py parakeet # ~630 MB model into models/
```

## Layout

```
sst/
├─ web.cmd                    double-click: web page
├─ start.cmd                  double-click: terminal version
├─ scripts/download_model.py  fetches models into models/
├─ models/                    downloaded models (git-ignored)
├─ recordings/                your recordings + transcripts (git-ignored)
└─ sst/
   ├─ cli.py                  the `sst` command
   ├─ web.py                  local server for the web page (127.0.0.1 only)
   ├─ static/index.html       the Record / Stop page
   ├─ audio.py                microphone recording, WAV read/write, saving recordings
   └─ engines/
      ├─ __init__.py          engine list + load_engine()
      └─ parakeet.py          Parakeet via sherpa-onnx (CPU)
```

## Adding another engine

Create `sst/engines/<name>.py` with a class that has `name` and `transcribe(audio, sample_rate) -> str`,
then add it to `ENGINES` and `load_engine()` in `sst/engines/__init__.py`. Use it with `uv run sst --engine <name> start`.
