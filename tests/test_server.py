import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_running_coach"))
from app.runtime import Runtime
from app.server import CoachServer, profile_fields, profile_markdown


class ServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runtime = Runtime(Path(directory.name))
        self.server = CoachServer(("127.0.0.1", 0), self.runtime, development=True)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=20)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def session(self):
        code, body, headers = self.request("GET", "/api/status")
        self.assertEqual(code, 200)
        return {
            "Cookie": headers["Set-Cookie"].split(";")[0],
            "X-Coach-CSRF": json.loads(body)["csrf"],
            "Content-Type": "application/json",
        }

    def test_peer_gate_ignores_forwarded_addresses(self):
        self.server.development = False
        self.assertEqual(self.request("GET", "/healthz")[0], 200)
        self.assertEqual(self.request("GET", "/", headers={"X-Forwarded-For": "172.30.32.2"})[0], 403)
        self.assertEqual(self.request("GET", "/api/status")[0], 403)

    def test_allowlist_and_opaque_dashboard(self):
        for path in ("/.env", "/core/README.md", "/assets/../server.py", "/assets/%2e%2e/server.py", "/training_plan.json"):
            self.assertEqual(self.request("GET", path)[0], 404)
        code, _, _ = self.request("GET", "/assets/coach.js")
        self.assertEqual(code, 200)
        with patch.object(self.runtime, "dashboard", return_value=b"<html>Saved dashboard</html>"):
            code, _, headers = self.request("GET", "/dashboard")
        self.assertEqual(code, 200)
        self.assertIn("sandbox allow-scripts allow-downloads", headers["Content-Security-Policy"])
        self.assertNotIn("allow-same-origin", headers["Content-Security-Policy"])

    def test_csrf_binding_limit_and_settings_save_preserve_profile(self):
        session = self.session()
        other = self.session()
        mismatch = {**session, "Cookie": other["Cookie"]}
        self.assertEqual(self.request("POST", "/api/config", "{}", mismatch)[0], 403)
        self.assertEqual(self.request("POST", "/api/config", " " * 17000, session)[0], 413)
        profile = {"name": "Synthetic runner", "days": ["Wednesday"], "zones": {"2": [140, 155]}}
        self.assertEqual(self.request("POST", "/api/profile", json.dumps(profile), session)[0], 200)
        before = self.runtime.profile()
        settings = {"provider": "gemini", "model": "gemini-3.5-flash-lite", "source": "offline", "gemini_api_key": "synthetic_key"}
        code, body, _ = self.request("POST", "/api/config", json.dumps(settings), session)
        self.assertEqual(code, 200)
        self.assertNotIn(b"synthetic_key", body)
        self.assertNotIn(b"synthetic_key", self.request("GET", "/api/status")[1])
        self.assertEqual(before, self.runtime.profile())
        self.assertEqual(self.request("POST", "/api/action", '{"action":"arbitrary"}', session)[0], 400)

    def test_unexpected_error_is_redacted(self):
        with patch.object(self.runtime, "status", side_effect=OSError("sensitive failure detail")):
            code, body, _ = self.request("GET", "/api/status")
        self.assertEqual(code, 500)
        self.assertNotIn(b"sensitive", body)

    def test_profile_merge_keeps_unrepresented_facts_and_other_event(self):
        old = """# Athlete profile
**Athlete Name:** Test runner
**Coaching Style:** Direct and brief.
**Preferred Rest Days:** Tuesday
**Preferred Long Run Day:** Saturday
**Zone 2 (Endurance):** 140 - 155 bpm
**Zone 5 (Max):** >= 190 bpm
**A-Race:** First event
**B-Race:** Second event
**Date:** 2027-05-16
"""
        fields = profile_fields(old)
        self.assertEqual(fields["race_date"], "")
        fields["goal"] = "Build consistency"
        updated = profile_markdown(fields, old)
        self.assertIn("Direct and brief. Training goal: Build consistency", updated)
        self.assertIn("**Preferred Rest Days:** Tuesday", updated)
        self.assertIn("**Zone 5 (Max):** >= 190 bpm", updated)
        self.assertIn("**B-Race:** Second event\n**Date:** 2027-05-16", updated)


if __name__ == "__main__":
    unittest.main()
