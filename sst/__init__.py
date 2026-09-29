"""sst: record from the microphone and transcribe it locally."""
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_DIR / "models"
RECORDINGS_DIR = PROJECT_DIR / "recordings"
