from datetime import date
import unittest
from dashboard_panels import build_panels,duration,pace

class DashboardWorkflowTests(unittest.TestCase):
    def test_agenda_priorities_and_saved_ai_explanations(self):
        plan={'sessions':[{'id':'today','date':'2026-10-31','workout_type':'Easy Run','amount':'5 km','target':'Conversational effort'},{'id':'next','date':'2026-11-02','workout_type':'Long Run','amount':'12 km','target':'Terrain-aware effort'}]}
        coach={'weekly_priorities':[{'priority':'Recover well','reason':'Coach-selected priority','evidence':['Recorded workload']}],'assessment':{'reason':'Evidence has limits','evidence':['Observed sessions'],'uncertainties':['Subjective fatigue missing']},'next_review_trigger':'After the next long run'}
        result=build_panels(plan,{'2026-10-31':[{'type':'Run','distance_m':5000,'moving_time_s':1800,'elapsed_time_s':2000,'start_date':'2026-10-31','name':'Recorded run','elevation_gain_m':120}]},coach,date(2026,10,31))
        self.assertIn('Today &amp; next workouts',result['agenda'])
        self.assertIn('does not confirm',result['agenda'])
        self.assertIn('Coach-selected priority',result['focus'])
        self.assertIn('Subjective fatigue missing',result['assessment'])
        self.assertIn('After the next long run',result['assessment'])
        self.assertIn('6:00 /km moving',result['history'])
        self.assertIn('33m 20s',result['history'])
        self.assertIn('Garmin activity note/description',result['feedback'])
        self.assertIn('coach_checkin.py',result['feedback'])
        self.assertNotIn('Strava',result['feedback'])
        self.assertNotIn('<form',result['feedback'])

    def test_zero_missing_and_untrusted_panel_text(self):
        plan={'sessions':[{'id':'a','date':'2026-10-31','workout_type':'<img src=x onerror=alert(1)>','amount':'10 km','target':'<script>alert(1)</script>'}]}
        result=build_panels(plan,{}, {'weekly_priorities':[{'priority':'<script>x</script>','reason':'<img src=x>','evidence':[]}]},date(2026,10,31))
        self.assertNotIn('<script>',result['agenda'])
        self.assertNotIn('<img',result['focus'])
        self.assertEqual(duration(None),'Unavailable')
        self.assertEqual(duration(0),'0m 0s')
        self.assertEqual(pace(1000,None),'Unavailable')
        self.assertEqual(pace(1000,0),'Unavailable')
