import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timezone
from cron_daily_update import deferred_retry_eligible

class DeferredRetryTests(unittest.TestCase):
    def check(self, error="503 UNAVAILABLE", count=1, status="pending", hour=3, pipeline_errors=None, limit=2, last_kind='daily'):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/"pipeline_status.json").write_text(json.dumps({"attempted_at":"2026-11-03T03:00:00+00:00","errors":pipeline_errors if pipeline_errors is not None else ["Coach review pending; previous review retained"]}))
            c=sqlite3.connect(root/"ai_coach_master.sqlite")
            c.execute("CREATE TABLE coach_api_requests(id INTEGER PRIMARY KEY,job_fingerprint,status,error,started_at,request_kind)")
            c.execute("CREATE TABLE coach_review_jobs(fingerprint,status)")
            c.execute("INSERT INTO coach_review_jobs VALUES('abc',?)",(status,))
            for i in range(count):c.execute("INSERT INTO coach_api_requests VALUES(?,?,?,?,?,?)",(i,'abc','failed',error,f"2026-11-03T{hour:02}:00:00+00:00",last_kind if i==count-1 else 'daily'))
            c.commit();c.close()
            with patch.dict('os.environ', {'COACH_MAX_DAILY_REQUESTS':str(limit)}):
                return deferred_retry_eligible(root,datetime(2026,11,3,4,15,tzinfo=timezone.utc))[0]
    def test_transient_remaining_slot(self):self.assertTrue(self.check())
    def test_normalized_provider_error(self):self.assertTrue(self.check("gemini API HTTP 503: Temporarily unavailable"))
    def test_anthropic_overload(self):self.assertTrue(self.check("anthropic API HTTP 529: Overloaded"))
    def test_no_quota_auth_validation_retry(self):
        for error in ["429 RESOURCE_EXHAUSTED","403 permission","Invalid JSON"]:
            with self.subTest(error=error):self.assertFalse(self.check(error))
    def test_spent_budget_no_retry(self):self.assertFalse(self.check(count=2))
    def test_configured_budget_is_honored(self):
        self.assertFalse(self.check(count=1,limit=1))
        self.assertTrue(self.check(count=2,limit=3))
    def test_approved_extra_is_never_automatically_retried(self):
        for kind in ('manual_override','operator_recovery'):
            with self.subTest(kind=kind):self.assertFalse(self.check(count=3,limit=5,last_kind=kind))
    def test_success_no_retry(self):self.assertFalse(self.check(status="complete"))
    def test_cooldown(self):self.assertFalse(self.check(hour=4))
    def test_unrelated_failure_no_retry(self):self.assertFalse(self.check(pipeline_errors=["Activity sync failed; showing saved data"]))
    def test_successful_pipeline_no_retry(self):self.assertFalse(self.check(pipeline_errors=[]))
