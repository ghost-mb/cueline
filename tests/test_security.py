"""The safe fetcher (feeds.fetch) must never reach this machine or the local network, even via DNS rebinding."""
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

os.environ.setdefault("CUELINE_DATA", tempfile.mkdtemp(prefix="cueline-test-"))

from cueline import feeds  # noqa: E402


class LocalSecret(BaseHTTPRequestHandler):
    hits = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        LocalSecret.hits += 1
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"secret")


class SafeFetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), LocalSecret)
        cls.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        LocalSecret.hits = 0

    def test_localhost_link_is_refused(self):
        with self.assertRaisesRegex(ValueError, "private address"):
            feeds.fetch(f"http://127.0.0.1:{self.port}/")
        self.assertEqual(LocalSecret.hits, 0)

    def test_dns_rebinding_is_refused(self):
        real, answers = socket.getaddrinfo, iter(["93.184.216.34"] + ["127.0.0.1"] * 5)

        def attacker_dns(host, *args, **kwargs):  # first lookup looks public, later ones point at this machine
            if host == "rebind.example":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), self.port))]
            return real(host, *args, **kwargs)

        with mock.patch("socket.getaddrinfo", attacker_dns):
            with self.assertRaisesRegex(ValueError, "private address"):
                feeds.fetch(f"http://rebind.example:{self.port}/")
        self.assertEqual(LocalSecret.hits, 0)
