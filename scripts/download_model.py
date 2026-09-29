"""Download and unpack a speech model into ./models.

Usage:  uv run python scripts/download_model.py [parakeet]
"""
import sys
import tarfile
import urllib.request
from pathlib import Path

MODELS = {
    # Same model OpenWhispr offers as "Parakeet Unified EN 0.6B" (English only, int8, ~631 MB).
    "parakeet": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming.tar.bz2"
    ),
}

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def _progress(block_num: int, block_size: int, total: int) -> None:
    done = block_num * block_size
    pct = min(100, done * 100 // total) if total > 0 else -1
    if pct != getattr(_progress, "last", None):  # once per percent, not per 8 KB block (keeps CI logs small)
        _progress.last = pct
        print(f"\r  downloading... {pct:3d}%  ({done / 1e6:,.0f} / {total / 1e6:,.0f} MB)", end="", flush=True)


def download(name: str) -> Path:
    url = MODELS[name]
    MODELS_DIR.mkdir(exist_ok=True)
    archive = MODELS_DIR / url.rsplit("/", 1)[-1]
    target = MODELS_DIR / archive.name.removesuffix(".tar.bz2")

    if target.exists():
        print(f"Already downloaded: {target}")
        return target

    print(f"Downloading {name} model from {url}")
    urllib.request.urlretrieve(url, archive, _progress)
    print("\n  extracting...")
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(MODELS_DIR, filter="data")
    archive.unlink()
    print(f"Done: {target}")
    return target


if __name__ == "__main__":
    download(sys.argv[1] if len(sys.argv) > 1 else "parakeet")
