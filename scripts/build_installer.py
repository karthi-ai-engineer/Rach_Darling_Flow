"""Build the Windows installer: dist/SST-Dictation-Setup-<version>.exe.

Run build_installer.cmd, not this file: it uses its own environment (build/venv), so a copy of
sst running from .venv doesn't get in the way.

  1. PyInstaller turns the app into dist/sst/ (sst.exe plus Python and the libraries)
  2. the Parakeet model is added as dist/sst/models/
  3. smoke test: dist/sst/sst.exe must transcribe the model's test recording
  4. Inno Setup packs dist/sst/ into a single setup .exe   (skipped with --no-installer)
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


def smoke_test() -> None:
    wav = MODEL_DIR / "test_wavs" / "0.wav"
    result = subprocess.run([str(APP_DIR / "sst.exe"), "file", str(wav)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=300)
    text = next((line.split("Text:", 1)[1].strip() for line in result.stdout.splitlines() if "Text:" in line), "")
    if result.returncode != 0 or not text or text == "(nothing recognised)":
        sys.exit(f"Smoke test failed: sst.exe did not transcribe {wav.name}\n{result.stdout}\n{result.stderr}")
    print(f"  sst.exe transcribed {wav.name}: {text!r}")
    # The tray app has no console to report into: it builds every window off-screen, transcribes, and sets the exit code.
    gui = subprocess.run([str(APP_DIR / "SST Dictation.exe"), "--self-test"], stdin=subprocess.DEVNULL, timeout=300)
    if gui.returncode != 0:
        sys.exit(f"Smoke test failed: SST Dictation.exe --self-test exited with {gui.returncode} "
                 f"(see %LOCALAPPDATA%\\sst\\logs)")
    print("  SST Dictation.exe --self-test passed (windows, Qt plugins, model)")


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
    return ROOT / "dist" / f"SST-Dictation-Setup-{__version__}.exe"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-installer", action="store_true", help="stop after the smoke test (no Inno Setup needed)")
    args = parser.parse_args()

    steps = [("Building the app with PyInstaller", build_app), ("Adding the model", add_model),
             ("Smoke test", smoke_test)]
    if not args.no_installer:
        steps.append(("Packing the installer with Inno Setup (a few minutes)", build_installer))
    t0 = time.perf_counter()
    for number, (title, step) in enumerate(steps, 1):
        print(f"[{number}/{len(steps)}] {title}...", flush=True)
        result = step()
    print(f"\nDone in {time.perf_counter() - t0:.0f}s: {result if isinstance(result, Path) else APP_DIR}")


if __name__ == "__main__":
    main()
