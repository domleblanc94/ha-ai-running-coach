from datetime import date
import unittest
import shutil
import subprocess
from pathlib import Path
from dashboard_panels import training_chart_data


class TrainingChartTests(unittest.TestCase):
    def test_known_quantities_units_plan_extent_and_unknown_load(self):
        sessions = [
            {'date':'2026-01-01','sport':'swimming','amount':'1500 m'},
            {'date':'2026-02-01','sport':'running','amount':'4 mi'},
            {'date':'2026-03-01','sport':'running','amount':'40 min'},
            {'date':'2026-03-02','sport':'running','amount':'5 or 8 km'},
            {'date':'2026-03-03','sport':'rest','amount':'Rest'}]
        rows = {'2026-01-01': [{'type':'lap_swimming','distance_m':1500,'training_load':None}],
                '2026-01-02': [{'type':'running','distance_m':None,'training_load':float('nan')}],
                '2027-01-01': [{'type':'running','distance_m':5000}]}
        data=training_chart_data(rows,sessions,date(2026,1,5))
        self.assertEqual(data['plan_start'],'2026-01-01')
        self.assertEqual(data['plan_end'],'2026-03-03')
        self.assertEqual(data['first_week'],'2025-12-29')
        self.assertEqual(data['last_week'],'2026-03-02')
        self.assertEqual(data['planned'][0]['km'],1.5)
        self.assertAlmostEqual(data['planned'][1]['km'],6.437376)
        self.assertEqual(data['planned'][2]['minutes'],40)
        self.assertNotIn('km',data['planned'][2])
        self.assertNotIn('km',data['planned'][3])
        self.assertTrue(all('load' not in row for row in data['planned']))
        self.assertEqual(len(data['actual']),2)
        self.assertEqual(data['actual'][0]['km'],1.5)
        self.assertNotIn('km',data['actual'][1])
        self.assertNotIn('load',data['actual'][1])

    @unittest.skipUnless(shutil.which("node"), "Node required for chart interaction checks")
    def test_chart_interactions_and_persistence(self):
        subprocess.run([shutil.which("node"), str(Path(__file__).with_suffix(".js"))], check=True, capture_output=True, text=True, timeout=20)

    def test_no_plan_keeps_current_week(self):
        data=training_chart_data({},[],date(2026,11,27))
        self.assertEqual(data['first_week'],'2026-11-23')
        self.assertEqual(data['last_week'],'2026-11-23')


if __name__=='__main__':unittest.main()
