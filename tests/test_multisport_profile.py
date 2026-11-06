import unittest

from ai_running_coach.app import hr_learning
from ai_running_coach.app.server import profile_fields, profile_markdown, profile_view


LEGACY = '''# Athlete profile
**Athlete Name:** Runner
**Weekly Availability:** Saturday, Sunday
**Preferred Long Run Day:** Saturday
**Running Availability:** Saturday
**Zone 2 (My easy range):** 130 - 145 bpm
**Zone 5 (Max):** >= 190 bpm
**HR Zone Learning:** Enabled
**A-Race:** Existing running race
**Date:** 2027-11-21
**Target Time:** Just finish
'''


class MultisportProfileTests(unittest.TestCase):
    def test_shared_zone_boundaries_and_inactive_raw_running_zones_are_preserved(self):
        raw = '**Athlete Name:** Runner\n**Zone 1 (Recovery):** 100 - 130 bpm\n**Zone 2 (Easy):** 130 - 150 bpm\n'
        result = profile_markdown({'name': 'Runner', 'training_mode': 'cycling'}, raw)
        self.assertIn('**Zone 2 (Easy):** 130 - 150 bpm', result)
        only_one = profile_markdown({'name': 'Runner', 'training_mode': 'swimming'}, raw.split('**Zone 2')[0])
        self.assertIn('**Zone 1 (Recovery):** 100 - 130 bpm', only_one)
        self.assertIn('**Cycling Zone 2', profile_markdown({'name': 'Runner', 'training_mode': 'cycling',
            'sport_settings': {'cycling': {'zones': {'1': [100, 130], '2': [130, 150]}}}}, raw))
        with self.assertRaises(ValueError):
            profile_markdown({'name': 'Runner', 'sport_settings': {'cycling': {'zones': {'2': [150, 256]}}}}, raw)

    def test_running_default_and_focus_edit_preserve_legacy_goal_and_hidden_settings(self):
        self.assertEqual(profile_view(LEGACY)['training_mode'], 'running')
        self.assertTrue(hr_learning.learning_enabled(LEGACY))
        changed = profile_markdown({'name': 'Runner', 'training_mode': 'cycling', 'days': ['Monday', 'Wednesday']}, LEGACY)
        for line in ('**Preferred Long Run Day:** Saturday', '**Running Availability:** Saturday',
                     '**Zone 2 (My easy range):** 130 - 145 bpm', '**Zone 5 (Max):** >= 190 bpm',
                     '**HR Zone Learning:** Enabled'):
            self.assertIn(line, changed)
        self.assertNotIn('sport', profile_fields(changed)['goals']['A'])
        self.assertFalse(hr_learning.learning_enabled(changed))
        view = profile_view(changed)
        self.assertEqual(view['sport_settings']['cycling']['zones'], {})
        self.assertIsNone(view['sport_settings']['cycling']['ftp_watts'])
        self.assertIsNone(view['sport_settings']['swimming']['pool_length_metres'])

    def test_goal_sport_is_explicit_and_separate_from_training_mode(self):
        changed = profile_markdown({'name': 'Runner', 'training_mode': 'triathlon', 'goals': {
            'B': {'event': 'New triathlon', 'kind': 'race', 'sport': 'multisport'},
        }}, LEGACY)
        goals = profile_fields(changed)['goals']
        self.assertNotIn('sport', goals['A'])
        self.assertEqual(goals['B']['sport'], 'multisport')
        self.assertTrue(hr_learning.learning_enabled(changed))
        self.assertIn('**Target Time:** Just finish', changed)
        preserved = profile_markdown({'name': 'Runner', 'goals': {'B': {'event': 'Renamed triathlon', 'kind': 'race'}}}, changed)
        self.assertEqual(profile_fields(preserved)['goals']['B']['sport'], 'multisport')

    def test_optional_settings_roundtrip_in_original_units_without_zone_reuse(self):
        changed = profile_markdown({'name': 'Runner', 'training_mode': 'triathlon', 'days': ['Saturday', 'Sunday'],
            'sport_settings': {'cycling': {'available_days': ['Sunday'], 'preferred_long_days': ['Sunday'],
                                           'ftp_watts': 230, 'zones': {'2': [120, 135]}},
                               'swimming': {'css_seconds_per_100m': 125, 'pool_length_metres': 22.86,
                                            'pool_length_unit': 'yd', 'sub_sport': 'lap_swimming'}}}, LEGACY)
        self.assertIn('**Pool Length:** 25 yd', changed)
        self.assertIn('**Swimming CSS:** 2:05 /100m', changed)
        self.assertIn('**Cycling FTP:** 230 W', changed)
        view = profile_view(changed)['sport_settings']
        self.assertAlmostEqual(view['swimming']['pool_length_metres'], 22.86)
        self.assertEqual(view['cycling']['zones'], {'2': [120, 135]})
        self.assertEqual(view['swimming']['zones'], {})
        patched = profile_markdown({'name': 'Runner', 'sport_settings': {'cycling': {'ftp_watts': None}}}, changed)
        self.assertNotIn('**Cycling FTP:**', patched)
        self.assertIn('**Cycling Zone 2 (Endurance):** 120 - 135 bpm', patched)
        self.assertIn('**Pool Length:** 25 yd', patched)

    def test_active_availability_and_measurement_validation(self):
        for settings in ({'cycling': {'available_days': ['Sunday']}},
                         {'cycling': {'ftp_watts': True}},
                         {'swimming': {'css_seconds_per_100m': -1}},
                         {'swimming': {'pool_length_metres': 700, 'pool_length_unit': 'm'}},
                         {'swimming': {'pool_length_metres': 25, 'pool_length_unit': None}}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                profile_markdown({'name': 'Runner', 'training_mode': 'triathlon', 'days': ['Saturday'], 'sport_settings': settings}, LEGACY)


if __name__ == '__main__':
    unittest.main()
