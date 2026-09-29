"""Gemeinsame Fixtures: server.py importierbar machen und die drei externen
Dienste (Whisper, Diarisation, ollama) durch eine Attrappe ersetzen.

Die Attrappe haengt sich an httpx.AsyncClient, nicht an einzelne Funktionen
in server.py — so ueberstehen die Endpoint-Tests einen Umbau der Interna.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402

# Ein kurzes Zwei-Personen-Gespraech mit Wort-Zeitstempeln.
WORDS = [
    (" Guten", 0.0, 0.4), (" Tag.", 0.4, 0.8),
    (" Was", 1.0, 1.2), (" führt", 1.2, 1.5), (" Sie", 1.5, 1.7), (" her?", 1.7, 2.0),
    (" Ich", 3.0, 3.2), (" habe", 3.2, 3.4), (" seit", 3.4, 3.6), (" drei", 3.6, 3.8),
    (" Tagen", 3.8, 4.1), (" Kopfschmerzen.", 4.1, 4.8),
]
SEGMENTS = [
    {"start": 0.0, "end": 2.1, "speaker": "SPEAKER_00"},
    {"start": 2.9, "end": 5.0, "speaker": "SPEAKER_01"},
]
LABELED = (
    "SPRECHER A: Guten Tag. Was führt Sie her?\n"
    "SPRECHER B: Ich habe seit drei Tagen Kopfschmerzen."
)


class FakeServices:
    def __init__(self):
        self.words = list(WORDS)
        self.text = "".join(w for w, _, _ in WORDS).strip()
        self.segments = [dict(s) for s in SEGMENTS]
        self.whisper_fails = False      # jede Whisper-Anfrage -> 500
        self.verbose_fails = False      # nur verbose_json -> 500
        self.diarize_status = 200
        self.llm_tokens = ["Hallo ", "Welt"]
        self.llm_done_reason = "stop"
        self.llm_error = None
        self.subject = "Kopfschmerzen seit drei Tagen"
        self.down: set = set()          # Hosts, die nicht antworten
        self.requests: list = []        # (host, path, body)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host, path, body = request.url.host, request.url.path, request.content
        self.requests.append((host, path, body))
        if host in self.down:
            raise httpx.ConnectError("down", request=request)

        if path == "/v1/audio/transcriptions":
            if self.whisper_fails or (self.verbose_fails and b"verbose_json" in body):
                return httpx.Response(500, text="kaputt")
            if b"verbose_json" in body:
                return httpx.Response(200, json={
                    "text": self.text,
                    "segments": [{"start": 0.0, "end": 4.8, "text": self.text}],
                    "words": [{"word": w, "start": s, "end": e} for w, s, e in self.words],
                })
            return httpx.Response(200, text=self.text + "\n")

        if path == "/diarize":
            if self.diarize_status != 200:
                return httpx.Response(self.diarize_status, text="Diarisation kaputt")
            return httpx.Response(200, json={"segments": self.segments})

        if path == "/api/generate":
            payload = json.loads(body)
            if not payload["stream"]:
                return httpx.Response(200, json={"response": f"<think>hmm</think>{self.subject}"})
            if self.llm_error:
                lines = [{"error": self.llm_error}]
            else:
                lines = [{"response": t, "done": False} for t in self.llm_tokens]
                lines.append({"response": "", "done": True, "done_reason": self.llm_done_reason,
                              "prompt_eval_count": 10, "eval_count": 2})
            return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines) + "\n")

        if path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "gemma4:12b"}, {"name": "qwen3:8b"}, {"name": "nomic-embed-text"},
            ]})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/":
            return httpx.Response(200, text="ok")
        return httpx.Response(404)

    def prompts(self) -> list:
        return [json.loads(b)["prompt"] for _, p, b in self.requests if p == "/api/generate"]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    services = FakeServices()
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(services.handle)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(server, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(server, "logging_enabled", False)
    return services


@pytest.fixture
def client(fake):
    return TestClient(server.app)


def sse_events(response) -> list:
    """SSE-Strom als Liste von Events; aufeinanderfolgende Tokens verschmolzen.

    Wie der ThinkingFilter die Tokens portioniert, ist kein Verhalten, auf das
    sich jemand verlaesst — der zusammengesetzte Text schon.
    """
    events = []
    for block in response.text.split("\n\n"):
        if not block.startswith("data: "):
            continue
        evt = json.loads(block[6:])
        if evt["type"] == "token" and events and events[-1]["type"] == "token":
            events[-1]["text"] += evt["text"]
        else:
            events.append(evt)
    return events
