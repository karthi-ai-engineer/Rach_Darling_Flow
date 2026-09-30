"""Download and unpack a speech model into ./models, with the word-piece vocabulary that hotwords need.

Usage:  uv run python scripts/download_model.py [parakeet]
"""
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

MODELS = {
    # Same model OpenWhispr offers as "Parakeet Unified EN 0.6B" (English only, int8, ~631 MB).
    "parakeet": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming.tar.bz2"
    ),
}

# NVIDIA's original model (a 2.5 GB tar archive). Only its tokenizer vocabulary (10 KB) is read, with HTTP range
# requests: sherpa-onnx needs it as bpe.vocab to spell "Your words" in the model's word pieces (hotwords), and the
# sherpa-onnx download above doesn't include it.
NEMO = {
    "parakeet": "https://huggingface.co/nvidia/parakeet-unified-en-0.6b/resolve/main/parakeet-unified-en-0.6b.nemo",
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
    else:
        print(f"Downloading {name} model from {url}")
        urllib.request.urlretrieve(url, archive, _progress)
        print("\n  extracting...")
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(MODELS_DIR, filter="data")
        archive.unlink()
        print(f"Done: {target}")
    if name in NEMO and not (target / "bpe.vocab").exists():
        fetch_vocab(NEMO[name], target)
    return target


def _range(url: str, start: int, size: int) -> bytes:
    for attempt in range(5):  # a first connection sometimes stalls on the dev laptop; a retry gets through
        try:
            request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{start + size - 1}"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def fetch_vocab(url: str, target: Path) -> None:
    """Find the tokenizer's .vocab in the .nemo archive by reading tar headers, fetch only that file, and keep it as
    bpe.vocab if its word pieces are the model's (tokens.txt, in the same order)."""
    print("Fetching the word-piece vocabulary for hotwords...")
    offset, name = 0, ""
    for _ in range(50):
        header = _range(url, offset, 512)
        if not header.strip(b"\0"):
            break
        size = int(header[124:136].rstrip(b"\0 ") or b"0", 8)
        if header[156:157] == b"x":  # a pax header: it holds the next entry's real name
            records = _range(url, offset + 512, size).decode("utf-8", "replace")
            name = next((line.split("path=", 1)[1] for line in records.splitlines() if "path=" in line), "")
        else:
            name = name or header[:100].rstrip(b"\0").decode("utf-8", "replace")
            if name.endswith("tokenizer.vocab"):
                vocab = _range(url, offset + 512, size).decode("utf-8")
                pieces = [line.split("\t")[0] for line in vocab.splitlines()]
                tokens = [line.rsplit(" ", 1)[0]
                          for line in (target / "tokens.txt").read_text(encoding="utf-8").splitlines()]
                if pieces != tokens[:len(pieces)]:
                    sys.exit("The vocabulary doesn't match the model's tokens; hotwords stay off.")
                (target / "bpe.vocab").write_text(vocab, encoding="utf-8", newline="\n")
                print(f"  bpe.vocab: {len(pieces)} word pieces")
                return
            name = ""
        offset += 512 + (size + 511) // 512 * 512
    sys.exit("No tokenizer vocabulary found in the model archive; hotwords stay off.")


if __name__ == "__main__":
    download(sys.argv[1] if len(sys.argv) > 1 else "parakeet")
