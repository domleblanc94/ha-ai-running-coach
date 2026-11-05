import json
import os
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from publish_if_ready import ready

class PublishGuardTests(unittest.TestCase):
    def test_freshness_errors_and_source_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);now=datetime.now(timezone.utc);stamp=(now-timedelta(minutes=5)).isoformat()
            state={"attempted_at":stamp,"sync_success_at":stamp,"errors":[]}
            (p/"pipeline_status.json").write_text(json.dumps(state))
            for name in ("athlete.md","training_plan.json","ai_coach_master.sqlite"):
                (p/name).write_text("saved")
                os.utime(p/name,(now.timestamp()-120,now.timestamp()-120))
            (p/"dashboard.html").write_text("rendered")
            self.assertTrue(ready(p,now)[0])
            state["errors"]=["review pending"]
            (p/"pipeline_status.json").write_text(json.dumps(state))
            self.assertFalse(ready(p,now)[0])
            state["errors"]=[]
            (p/"pipeline_status.json").write_text(json.dumps(state))
            self.assertFalse(ready(p,now+timedelta(hours=7))[0])
            os.utime(p/"training_plan.json",(now.timestamp()+1,now.timestamp()+1))
            self.assertFalse(ready(p,now)[0])

    def test_incomplete_state_never_uploads(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            self.assertFalse(ready(p)[0])
            (p/".plan-commit.json").write_text("{}")
            self.assertFalse(ready(p)[0])
