from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

from ai_running_coach.app.exports import calendar_pdf


def session(day='2026-11-20'):
    return {'id': 'synthetic', 'date': day, 'sport': 'running', 'workout_type': 'Easy run',
            'amount': '30 minutes', 'target': 'Comfortable conversational effort'}


class PhaseExportTests(unittest.TestCase):
    def render(self, plan, **kwargs):
        from reportlab.platypus import Paragraph
        from reportlab.pdfgen.canvas import Canvas
        paragraphs, pages = [], []

        def paragraph(value, *args, **options):
            paragraphs.append(value)
            return Paragraph(value, *args, **options)

        class RecordingCanvas(Canvas):
            def showPage(self):
                pages.append(self.getPageNumber())
                return super().showPage()

        before = json.dumps(plan, sort_keys=True)
        with patch('reportlab.platypus.Paragraph', side_effect=paragraph), patch('reportlab.pdfgen.canvas.Canvas', RecordingCanvas):
            document = calendar_pdf(plan, 'Synthetic athlete', **kwargs)
        self.assertTrue(document.startswith(b'%PDF-'))
        self.assertTrue(document.rstrip().endswith(b'%%EOF'))
        self.assertEqual(json.dumps(plan, sort_keys=True), before)
        return '\n'.join(paragraphs), pages

    def test_partial_transition_week_includes_exact_ranges_gaps_and_full_purposes(self):
        plan = {'revision': 2, 'sessions': [session()], 'phases': [
            {'start_date': '2026-11-01', 'end_date': '2026-11-18', 'name': 'Baseline building', 'purpose': 'Establish comfortable consistency.'},
            {'start_date': '2026-11-20', 'end_date': '2026-11-21', 'name': 'Build <carefully>', 'purpose': 'Build durability & preserve recovery.'},
            {'start_date': '2026-11-22', 'end_date': '2026-12-02', 'name': 'Taper', 'purpose': 'Reduce load while retaining familiarity.'},
        ]}
        text, pages = self.render(plan, conflicts=[{'date': '2026-11-20'}])
        for expected in ('Baseline building | 2026-11-16 - 2026-11-18',
                         'Phase not specified | 2026-11-19 - 2026-11-19',
                         'Build &lt;carefully&gt; | 2026-11-20 - 2026-11-21',
                         'Taper | 2026-11-22 - 2026-11-22',
                         'Build durability &amp; preserve recovery.'):
            self.assertIn(expected, text)
        self.assertNotIn('<carefully>', text)
        self.assertEqual(len(pages), 3)  # Calendar, full phase overview, session details.

    def test_long_phase_overview_flows_without_dropping_names_or_purposes(self):
        phases = []
        for index in range(16):
            start = date(2026, 11, 1) + timedelta(days=index * 7)
            phases.append({'start_date': start.isoformat(), 'end_date': (start + timedelta(days=6)).isoformat(),
                           'name': f'Block {index:02d} ' + 'Progressive durability and consistent practice'[:50],
                           'purpose': f'Purpose {index:02d}: ' + ('Use familiar sessions to practise pacing and observe recovery. ' * 4)[:220]})
        text, pages = self.render({'revision': 3, 'sessions': [session()], 'phases': phases})
        self.assertGreater(len(pages), 3)
        for phase in phases:
            self.assertIn(phase['name'], text)
            self.assertIn(phase['purpose'], text)

    def test_legacy_plan_remains_unassigned_and_is_not_mutated(self):
        plan = {'revision': 1, 'sessions': [session()]}
        text, pages = self.render(plan)
        self.assertIn('Phase not specified | 2026-11-16 - 2026-11-22', text)
        self.assertNotIn('Taper', text)
        self.assertNotIn('Baseline building', text)
        self.assertEqual(len(pages), 2)


if __name__ == '__main__':
    unittest.main()
