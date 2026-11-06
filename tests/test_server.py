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
        self.assertEqual(fields["goals"]["A"]["race_date"], "")
        fields["goal"] = "Build consistency"
        updated = profile_markdown(fields, old)
        self.assertIn("Direct and brief. Training goal: Build consistency", updated)
        self.assertIn("**Preferred Rest Days:** Tuesday", updated)
        self.assertIn("**Zone 5 (Max):** >= 190 bpm", updated)
        self.assertIn("**B-Race:** Second event\n**Date:** 2027-05-16", updated)

    def test_three_goals_keep_dates_and_targets_with_their_priorities(self):
        old = """**Athlete Name:** Test runner
**Heart Rate Zones:** Not configured
**A-Race:** Main event
**Date:** November 21, 2027
**Target Time:** Finish
**B-Race:** Secondary event
**Date:** 2027-05-16
**Target Time:** Sub 2 hours
**C-Race:** Run consistently
**Preferred Rest Days:** Thursday
"""
        fields = profile_fields(old)
        self.assertEqual(fields["goals"]["A"]["race_date"], "2027-11-21")
        self.assertEqual(fields["goals"]["C"]["race_date"], "")
        fields["goals"]["A"] = {}
        updated = profile_markdown(fields, old)
        self.assertNotIn("Main event", updated)
        self.assertNotIn("2027-11-21", updated)
        self.assertIn("**Preferred Rest Days:** Thursday", updated)
        result = profile_fields(updated)["goals"]
        self.assertEqual(result["B"]["target_time"], "Sub 2 hours")
        self.assertEqual(result["B"]["race_date"], "2027-05-16")
        self.assertEqual(result["C"]["event"], "Run consistently")

    def test_optional_long_run_alternatives_roundtrip_and_preserve_legacy_preference(self):
        old = "**Athlete Name:** Runner\n**Heart Rate Zones:** Not configured\n**Weekly Availability:** Saturday, Sunday\n**Preferred Long Run Day:** Sunday\n**Preferred Rest Days:** Tuesday\n"
        fields = profile_fields(old)
        self.assertEqual(fields['long_run_days'], ['Sunday'])
        without_field = {key: value for key, value in fields.items() if key != 'long_run_days'}
        self.assertIn('**Preferred Long Run Day:** Sunday', profile_markdown(without_field, old))
        fields['long_run_days'] = ['Sunday', 'Saturday']
        updated = profile_markdown(fields, old)
        self.assertIn('**Preferred Long Run Days:** Saturday, Sunday', updated)
        self.assertNotIn('**Preferred Long Run Day:**', updated)
        self.assertIn('**Preferred Rest Days:** Tuesday', updated)
        self.assertEqual(profile_fields(updated)['long_run_days'], ['Saturday', 'Sunday'])
        fields['long_run_days'] = []
        cleared = profile_markdown(fields, updated)
        self.assertNotIn('**Preferred Long Run', cleared)
        self.assertEqual(profile_fields(cleared)['long_run_days'], [])

    def test_long_run_preferences_must_fit_explicit_availability(self):
        draft = {'name': 'Runner', 'days': ['Tuesday', 'Saturday'], 'long_run_days': ['Sunday']}
        with self.assertRaisesRegex(ValueError, 'available training days'):
            profile_markdown(draft)
        draft['days'] = []
        self.assertEqual(profile_fields(profile_markdown(draft))['long_run_days'], ['Sunday'])
        draft['long_run_days'] = []
        self.assertEqual(profile_fields(profile_markdown(draft))['long_run_days'], [])
        for invalid in ('Sunday', ['Someday'], [False], None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                profile_markdown({**draft, 'long_run_days': invalid})

    def test_preference_save_updates_profile_without_coaching_or_changing_schedule(self):
        session = self.session()
        before_schedule = self.runtime.status()['schedule']
        payload = {'name': 'Runner', 'days': ['Friday', 'Saturday'], 'long_run_days': ['Saturday']}
        code, body, _ = self.request('POST', '/api/profile', json.dumps(payload), session)
        self.assertEqual(code, 200, body)
        summary = json.loads(self.request('GET', '/api/profile-summary')[1])
        self.assertEqual(summary['profile']['long_run_days'], ['Saturday'])
        self.assertEqual(self.runtime.status()['schedule'], before_schedule)
        self.assertIsNone(self.runtime.status()['queued'])
        self.assertFalse((self.runtime.workspace / 'ai_coach_master.sqlite').exists())

    def test_unedited_free_text_long_run_preference_survives_profile_edit(self):
        self.runtime.save_profile('**Athlete Name:** Runner\n**Heart Rate Zones:** Not configured\n**Preferred Long Run Day:** Weekend mornings\n')
        session = self.session()
        saved = json.loads(self.request('GET', '/api/profile')[1])
        self.assertEqual(saved['long_run_days'], [])
        self.assertEqual(saved['long_run_preference_text'], 'Weekend mornings')
        code, body, _ = self.request('POST', '/api/profile', json.dumps({'name': 'Updated runner', 'days': ['Saturday']}), session)
        self.assertEqual(code, 200, body)
        self.assertIn('**Preferred Long Run Day:** Weekend mornings', self.runtime.profile())
        summary = json.loads(self.request('GET', '/api/profile-summary')[1])
        self.assertEqual(summary['profile']['long_run_preference_text'], 'Weekend mornings')
        code, _, _ = self.request('POST', '/api/profile', json.dumps({'name': 'Updated runner', 'long_run_days': []}), session)
        self.assertEqual(code, 200)
        self.assertNotIn('**Preferred Long Run Day:', self.runtime.profile())

    def test_learning_mode_keeps_saved_ranges_and_leaves_new_ranges_unknown(self):
        previous = "**Athlete Name:** Test runner\n**Zone 2 (Endurance):** 140 - 155 bpm\n**Zone 5 (Max):** >= 190 bpm\n"
        draft = {"name": "Test runner", "zone_mode": "learn"}
        saved = profile_markdown(draft, previous)
        self.assertIn("**Zone 2 (Endurance):** 140 - 155 bpm", saved)
        self.assertIn("**Zone 5 (Max):** >= 190 bpm", saved)
        self.assertEqual(profile_fields(saved)["zone_mode"], "learn")
        fresh = profile_markdown(draft)
        self.assertIn("**Heart Rate Zones:** Not configured", fresh)
        self.assertEqual(profile_fields(fresh)["zones"], {})

    def test_race_outcomes_roundtrip_as_one_goal_and_default_to_finish(self):
        draft = {
            "name": "Synthetic runner",
            "goals": {
                "A": {"event": "Marathon", "kind": "race", "race_date": "2027-05-15",
                      "outcomes": {"A": "Sub 3:45", "B": "Sub 4", "C": "Finish steadily"}},
                "B": {"event": "Trail event", "kind": "race"},
                "C": {"event": "Build consistency", "kind": "training", "race_date": "2027-06-01"},
            },
        }
        result = profile_markdown(draft)
        self.assertEqual(result.count("**A-Race:**"), 1)
        self.assertIn("**Target Time:** A (ideal): Sub 3:45 | B (main): Sub 4 | C (fallback): Finish steadily", result)
        self.assertIn("**B-Race:** Trail event\n**Target Time:** Just finish", result)
        fields = profile_fields(result)
        self.assertEqual(fields["goals"]["A"]["outcomes"], draft["goals"]["A"]["outcomes"])
        self.assertEqual(fields["goals"]["B"]["target_time"], "")
        self.assertEqual(fields["goals"]["C"]["kind"], "training")
        self.assertEqual(fields["goals"]["C"]["event"], "Build consistency")
        self.assertEqual(profile_markdown(fields, result), result)
        # The wrapper metadata is private to the UI. Confirm that the actual
        # pinned coaching parser receives the training intent in its goal text.
        core = Path(__file__).resolve().parents[1] / "ai_running_coach" / "core"
        with patch.object(sys, "path", [str(core), *sys.path]):
            from planning_engine import parse_profile
        parsed = parse_profile(result)
        self.assertEqual(parsed["goals"][2], {
            "priority": "C-Race", "event": "Training milestone: Build consistency", "date": "2027-06-01",
        })


if __name__ == "__main__":
    unittest.main()
