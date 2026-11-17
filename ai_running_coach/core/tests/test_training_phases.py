"""Bounded phase metadata, honest gaps and compact coach-owned labels."""
import copy
import unittest

import ai_coach
from planning_engine import parse_profile, render_plan, validate_plan
from training_phases import (validate_phases, phase_for_date, phases_overlapping,
                             phase_spans, replace_future_phases)


def phase(start='2026-11-17', end='2026-11-23', name='Build', purpose='Develop controlled endurance.'):
    return {'start_date': start, 'end_date': end, 'name': name, 'purpose': purpose}


class TrainingPhaseTests(unittest.TestCase):
    def test_schema_strict_dates_bounded_safe_text_and_inclusive_nonoverlap(self):
        valid = phase()
        values = [None, {}, [None], [dict(valid, extra='x')], [dict(valid, name=True)],
                  [dict(valid, purpose=' ')], [dict(valid, name='x' * 65)],
                  [dict(valid, purpose='x' * 241)], [dict(valid, name='Build\nTaper')],
                  [dict(valid, purpose='hidden\x00text')], [dict(valid, purpose='bad\ud800')],
                  [dict(valid, name='Line\u2028break')], [dict(valid, start_date='20261117')],
                  [dict(valid, start_date='2026-02-30')], [dict(valid, start_date='2026-11-24')],
                  [valid, phase(start='2026-11-23', end='2026-11-25')]]
        for value in values:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError): validate_phases(value)
        self.assertEqual(validate_phases([valid, phase('2026-11-24', '2026-11-25')]),
                         [valid, phase('2026-11-24', '2026-11-25')])
        many = [phase(f'2026-10-{day:02}', f'2026-10-{day:02}', name=f'Phase {day}') for day in range(1, 26)]
        self.assertEqual(len(validate_phases(many)), 25)  # Retained history has no response-size cap.
        with self.assertRaises(ValueError): validate_phases(many, max_items=24)

    def test_inclusive_lookup_and_spans_expose_unknown_gaps_without_mutating(self):
        first = phase('2026-11-17', '2026-11-19')
        second = phase('2026-11-21', '2026-11-23', 'Recovery', 'Absorb the preceding work.')
        plan = {'phases': [second, first]}
        before = copy.deepcopy(plan)
        self.assertEqual(phase_for_date(plan, '2026-11-19'), first)
        self.assertIsNone(phase_for_date(plan, '2026-11-20'))
        self.assertEqual(phases_overlapping(plan, '2026-11-19', '2026-11-21'), [first, second])
        spans = phase_spans(plan, '2026-11-18', '2026-11-24')
        self.assertEqual([(x['start_date'], x['end_date'], x['phase']['name'] if x['phase'] else None) for x in spans],
                         [('2026-11-18','2026-11-19','Build'), ('2026-11-20','2026-11-20',None),
                          ('2026-11-21','2026-11-23','Recovery'), ('2026-11-24','2026-11-24',None)])
        self.assertEqual(plan, before)
        self.assertEqual(phase_spans({}, '9999-12-31', '9999-12-31'),
                         [{'start_date': '9999-12-31', 'end_date': '9999-12-31', 'phase': None}])

    def test_future_replacement_preserves_meaning_and_coalesces_only_exact_adjacency(self):
        old = [phase('2026-11-10', '2026-11-22')]
        replacement = [phase('2026-11-17', '2026-11-20'), phase('2026-11-22', '2026-11-23')]
        before = copy.deepcopy((old, replacement))
        self.assertEqual(replace_future_phases(old, replacement, '2026-11-17'),
                         [phase('2026-11-10', '2026-11-20'), phase('2026-11-22', '2026-11-23')])
        self.assertEqual((old, replacement), before)
        self.assertEqual(replace_future_phases(old, [], '2026-11-17'), [phase('2026-11-10', '2026-11-16')])
        for invalid in (None, [phase('2026-11-16', '2026-11-20')], [phase('2026-11-17', '2027-11-19')]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError): replace_future_phases(old, invalid, '2026-11-17')

    def test_prompt_keeps_only_upcoming_phase_context_without_mandatory_coverage(self):
        profile = parse_profile('**Heart Rate Zones:** Not configured\n')
        plan = {'schema_version':1,'revision':1,'profile_hash':profile['source_hash'],'goals':profile['goals'],
                'sessions':[{'id':'run','date':'2026-11-20','workout_type':'Easy run','amount':'30 min','target':'Conversational effort'}],
                'phases':[phase('2026-10-01','2026-10-07','Earlier block','HISTORICAL_ONLY'),
                          phase('2026-11-10','2026-11-19')]}
        before = copy.deepcopy(plan)
        validate_plan(plan, profile)
        prompt = ai_coach.make_prompt(profile, plan, {'today':'2026-11-17','recent_7d':[]}, 'Review my plan')
        self.assertNotIn('HISTORICAL_ONLY', prompt)
        self.assertIn('"unlabelled_upcoming_dates": ["2026-11-20"]', prompt)
        self.assertIn('Providing phases replaces ALL today-onward', prompt)
        self.assertIn('Omitting phases in an ordinary review retains existing labels', prompt)
        self.assertIn('[] explicitly', prompt)
        self.assertIn('same response budget', prompt)
        self.assertEqual(plan, before)
        self.assertIn('v10-phases', ai_coach.PROMPT_VERSION)

    def test_markdown_phases_are_plain_safe_metadata_and_legacy_stays_unspecified(self):
        plan = {'goals':[], 'sessions':[{'id':'run','date':'2026-11-20','workout_type':'Easy run','amount':'30 min','target':'Easy effort'}]}
        self.assertIn('Phase not specified', render_plan(plan))
        labelled = copy.deepcopy(plan)
        labelled['phases'] = [phase(name='Build | **steady** <script>', purpose='[hint](https://example.test) # heading')]
        text = render_plan(labelled)
        self.assertIn('2026-11-17 to 2026-11-23', text)
        self.assertNotIn('<script>', text)
        self.assertNotIn('[hint](https://example.test)', text)
        self.assertNotIn('\n# heading', text)
        self.assertEqual([line for line in text.splitlines() if line.startswith('| ')],
                         ['| Day | Date | Workout Type | Distance / Duration | Target |',
                          '| Friday | 2026-11-20 | Easy run | 30 min | Easy effort |'])


if __name__ == '__main__': unittest.main()
