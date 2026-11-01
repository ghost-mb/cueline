"""The "Local model" provider (Ollama, LM Studio...): no key, talks to an OpenAI-compatible server on this machine."""
import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

os.environ.setdefault("CUELINE_DATA", tempfile.mkdtemp(prefix="cueline-test-"))

from cueline import db, llm  # noqa: E402
import _db  # noqa: E402  (tests/_db.py: empties the temp test database)


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # /v1/models
        body = json.dumps({"object": "list", "data": [{"id": "llama3.2", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # /v1/chat/completions, streamed
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "llama3.2",
                 "choices": [{"index": 0, "delta": {"content": "Hello from local"}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())


class LocalModel(unittest.TestCase):
    def setUp(self):
        _db.fresh()

    def use(self, url):
        p = mock.patch.dict(llm.PROVIDERS["local"], {"base_url": url})
        p.start()
        self.addCleanup(p.stop)

    def test_lists_models_and_answers_without_a_key(self):
        server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.use(f"http://127.0.0.1:{server.server_port}/v1")
        eng = llm.Engine()
        self.assertEqual([m["id"] for m in eng.list_models("local")], ["llama3.2"])
        db.set_setting("ai_provider", "local")
        db.set_setting("ai_model_local", "llama3.2")
        self.assertEqual(eng.generate("system", "hi"), "Hello from local")

    def test_server_not_running_says_what_to_do(self):
        with socket.socket() as s:  # a port nothing listens on
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.use(f"http://127.0.0.1:{port}/v1")
        with self.assertRaisesRegex(llm.LLMError, "Start Ollama or LM Studio"):
            llm.Engine().list_models("local")
