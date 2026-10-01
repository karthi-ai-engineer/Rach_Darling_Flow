"""Build the Windows installer: dist/Rflow-Setup-<version>.exe.

Run build_installer.cmd, not this file: it uses its own environment (build/venv), so a copy of
sst running from .venv doesn't get in the way.

  1. PyInstaller turns the app into dist/sst/ (Rflow.exe, rflow-cli.exe, Python and the libraries)
  2. the Parakeet model is linked in as dist/sst/models/, for the smoke test
  3. smoke test: rflow-cli.exe must transcribe the sample sentence, Rflow.exe must pass --self-test
  4. the model is taken out again: the installer comes without it, and Rflow downloads it when the user chooses it
     (an update from Rflow 1.4 keeps the copy that version installed)
  5. Inno Setup packs dist/sst/ into a single setup .exe   (steps 4-5 skipped with --no-installer)
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sst import __version__  # noqa: E402
from sst.engines.parakeet import MODEL_DIR  # noqa: E402

APP_DIR = ROOT / "dist" / "sst"
PACKAGING = ROOT / "packaging"
SAMPLE = ROOT / "sst" / "static" / "sample.wav"


def build_app() -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", str(PACKAGING / "sst.spec"), "--noconfirm", "--log-level", "WARN",
                    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller")], check=True)


def add_model() -> None:
    if not MODEL_DIR.exists():
        sys.exit(f"Model not found in {MODEL_DIR}\nDownload it first:  uv run python scripts/download_model.py parakeet")
    target = APP_DIR / "models" / MODEL_DIR.name
    target.mkdir(parents=True)
    for src in MODEL_DIR.iterdir():
        if src.is_file():  # everything but test_wavs/
            try:
                os.link(src, target / src.name)  # instant, and no second copy of 650 MB on disk
            except OSError:
                shutil.copy2(src, target / src.name)


def remove_model() -> None:
    shutil.rmtree(APP_DIR / "models")  # hard links: the model in models/ stays


def smoke_test() -> None:
    wav = SAMPLE
    result = subprocess.run([str(APP_DIR / "rflow-cli.exe"), "file", str(wav)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=300)
    text = next((line.split("Text:", 1)[1].strip() for line in result.stdout.splitlines() if "Text:" in line), "")
    if result.returncode != 0 or not text or text == "(nothing recognised)":
        sys.exit(f"Smoke test failed: rflow-cli.exe did not transcribe {wav.name}\n{result.stdout}\n{result.stderr}")
    print(f"  rflow-cli.exe transcribed {wav.name}: {text!r}")
    # The tray app has no console to report into: it builds every window off-screen, transcribes, and sets the exit code.
    gui = subprocess.run([str(APP_DIR / "Rflow.exe"), "--self-test"], stdin=subprocess.DEVNULL, timeout=300)
    if gui.returncode != 0:
        sys.exit(f"Smoke test failed: Rflow.exe --self-test exited with {gui.returncode} "
                 f"(see %LOCALAPPDATA%\\sst\\logs)")
    print("  Rflow.exe --self-test passed (windows, Qt plugins, model)")


def find_iscc() -> Path:
    candidates = [ROOT / ".tools" / "innosetup" / "ISCC.exe"]
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates += sorted(Path(base).glob("Inno Setup */ISCC.exe"))
            candidates += sorted(Path(base).glob("Programs/Inno Setup */ISCC.exe"))
    found = next((p for p in candidates if p.exists()), None) or (Path(p) if (p := shutil.which("iscc")) else None)
    if not found:
        sys.exit("Inno Setup 6 not found. Install it with:  winget install JRSoftware.InnoSetup")
    return found


def build_installer() -> Path:
    subprocess.run([str(find_iscc()), f"/DAppVersion={__version__}", "/Q", str(PACKAGING / "installer.iss")], check=True)
    return ROOT / "dist" / f"Rflow-Setup-{__version__}.exe"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-installer", action="store_true", help="stop after the smoke test (no Inno Setup needed)")
    args = parser.parse_args()

    steps = [("Building the app with PyInstaller", build_app), ("Adding the model for the smoke test", add_model),
             ("Smoke test", smoke_test)]
    if not args.no_installer:
        steps += [("Taking the model out (Rflow downloads it when chosen)", remove_model),
                  ("Packing the installer with Inno Setup (a minute)", build_installer)]
    t0 = time.perf_counter()
    for number, (title, step) in enumerate(steps, 1):
        print(f"[{number}/{len(steps)}] {title}...", flush=True)
        result = step()
    print(f"\nDone in {time.perf_counter() - t0:.0f}s: {result if isinstance(result, Path) else APP_DIR}")


if __name__ == "__main__":
    main()
