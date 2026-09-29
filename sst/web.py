"""Local web page: record in the browser, transcribe on this laptop.

The server only listens on 127.0.0.1, so nothing outside this laptop can reach it.
"""
import io
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sst.audio import load_wav, save_recording

PAGE = Path(__file__).parent / "static" / "index.html"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # ~100 minutes of 16 kHz audio


def serve(engine, port: int = 8765, open_browser: bool = True) -> None:
    lock = threading.Lock()  # one transcription at a time

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/info":
                self._json(200, {"engine": engine.name})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/transcribe":
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_UPLOAD_BYTES:
                return self._json(400, {"error": "empty or too large upload"})
            try:
                audio, rate = load_wav(io.BytesIO(self.rfile.read(length)))
                with lock:
                    t0 = time.perf_counter()
                    text = engine.transcribe(audio, rate)
                    took = time.perf_counter() - t0
                saved = save_recording(audio, rate, text)
            except Exception as e:
                return self._json(500, {"error": str(e)})

            duration = len(audio) / rate
            print(f"  {duration:5.1f}s audio -> {took:.2f}s  {text[:80]}")
            self._json(200, {"text": text, "audio_seconds": round(duration, 2),
                             "took_seconds": round(took, 2), "engine": engine.name, "saved": saved})

        def _json(self, status, obj):
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the console for transcripts only
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"\nOpen {url}  (close this window or press Ctrl+C to stop)\n")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
