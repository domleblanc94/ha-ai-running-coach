from datetime import date, timedelta
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from ai_running_coach.app.runtime import Runtime
from ai_running_coach.app.server import CoachServer, profile_markdown
from ai_running_coach.app.storage import file_lock


class ExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Runtime(Path(temporary.name))
        self.runtime.save_config({'gemini_api_key': 'private-key-never-exported'})
        self.profile = profile_markdown({
            'name': 'Synthetic runner', 'days': ['Monday', 'Wednesday'],
            'goals': {'A': {'kind': 'race', 'event': 'Synthetic event'}},
        }, '**Age:** 42\n**Preferred Long Run Day:** Wednesday\n')
        self.runtime.save_profile(self.profile)
        self.server = CoachServer(('127.0.0.1', 0), self.runtime, development=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def plan(self):
        sunday = date.today() + timedelta(days=(6 - date.today().weekday()) % 7)
        data = {'schema_version': 1, 'revision': 3, 'goals': [], 'sessions': [
            {'id': 'saved-sunday', 'date': sunday.isoformat(), 'workout_type': 'Easy run',
             'amount': '30 minutes', 'target': 'Comfortable effort <not markup> & a talk test.'},
        ]}
        (self.runtime.workspace / 'training_plan.json').write_text(json.dumps(data), encoding='utf-8')
        markdown = '# Training plan\n\nA saved calendar with a Sunday run.\n'
        (self.runtime.workspace / 'training_plan.md').write_text(markdown, encoding='utf-8')
        return (self.runtime.workspace / 'training_plan.md').read_bytes()

    def test_profile_summary_and_exact_markdown_exports_exclude_settings(self):
        code, body, _ = self.request('/api/profile-summary')
        self.assertEqual(code, 200)
        saved = json.loads(body)
        self.assertIn('**Age:** 42', saved['markdown'])
        self.assertIn('**Preferred Long Run Day:** Wednesday', saved['markdown'])
        self.assertNotIn('coach-goal', saved['markdown'])
        code, body, headers = self.request('/exports/athlete.md')
        self.assertEqual(code, 200)
        self.assertEqual(body, self.profile.encode())
        self.assertEqual(headers['Content-Disposition'], 'attachment; filename="athlete.md"')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn(b'private-key-never-exported', body)
        for path in ('/exports/.env', '/exports/../.env', '/exports/ai_coach_master.sqlite', '/exports/%2e%2e/.env'):
            self.assertEqual(self.request(path)[0], 404)
        markdown = self.plan()
        self.assertEqual(self.request('/exports/training_plan.md')[1], markdown)

    def test_pdf_download_and_conflicts_use_current_saved_plan_without_a_coach_call(self):
        self.assertEqual(self.request('/exports/training_calendar.pdf')[0], 404)
        self.plan()
        before = {name: (self.runtime.workspace / name).read_bytes()
                  for name in ('athlete.md', 'training_plan.json', 'training_plan.md', '.env')}
        status = self.runtime.status()
        self.assertEqual(status['training_day_conflicts'][0]['weekday'], 'Sunday')
        code, body, headers = self.request('/exports/training_calendar.pdf')
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b'%PDF-'))
        self.assertTrue(body.rstrip().endswith(b'%%EOF'))
        self.assertEqual(headers['Content-Type'], 'application/pdf')
        self.assertEqual(headers['Content-Disposition'], 'attachment; filename="training_calendar.pdf"')
        self.assertEqual(before, {name: (self.runtime.workspace / name).read_bytes() for name in before})
        self.assertFalse((self.runtime.workspace / 'ai_coach_master.sqlite').exists())
        self.runtime.save_profile(self.profile.replace('Monday, Wednesday', 'Monday, Wednesday, Sunday'))
        self.assertEqual(self.runtime.status()['training_day_conflicts'], [])

    def test_busy_commit_and_ingress_gate_block_downloads(self):
        self.plan()
        with file_lock(self.runtime.run_lock):
            self.assertEqual(self.request('/exports/training_calendar.pdf')[0], 409)
        (self.runtime.workspace / '.plan-commit.json').write_text('{}')
        self.assertEqual(self.request('/exports/training_plan.md')[0], 409)
        self.server.development = False
        self.assertEqual(self.request('/exports/athlete.md')[0], 403)

    def test_legacy_dashboard_table_hidden_without_removing_handler_targets(self):
        legacy = b'<div id="tableView"></div><button id="toggleViewBtn">Table View</button><div id="calendarView"></div>'
        (self.runtime.public / 'dashboard.html').write_bytes(legacy)
        code, body, _ = self.request('/dashboard')
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(legacy))
        self.assertIn(b'#toggleViewBtn,#tableView{display:none!important}', body)
        self.assertEqual((self.runtime.public / 'dashboard.html').read_bytes(), legacy)


if __name__ == '__main__':
    unittest.main()
