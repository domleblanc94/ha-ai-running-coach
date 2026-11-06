"""Execute the shipped calendar helper: dates, gaps and sessions stay distinct."""
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from generate_dashboard import build_dashboard


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
RUNNER = r'''
const fs=require('fs'),vm=require('vm');
const input=JSON.parse(process.argv[2]);
const registry=new Map();
const document={getElementById:id=>registry.get(id)||null};
class Element {
    constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.className='';this.ownerDocument=document;
        this.classList={add:(name)=>this.className+=' '+name};}
    appendChild(child){this.children.push(child);if(child.id)registry.set(child.id,child);return child;}
}
document.createElement=tag=>new Element(tag);
document.head=new Element('head');
const calendar=new Element('div'),header=new Element('div');
registry.set('calendarView',calendar);registry.set('calendarWeekdayHeader',header);
const scope={window:{},Date,Map,Set,console};vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
const api=scope.window.CoachCalendar,before=JSON.stringify(input.weeks),weeks=api.normalizeWeeks(input.weeks),renders=[];
for(const week of weeks){
    const grid=new Element('div'),calls=[];
    api.renderWeek(grid,week,(parent,...row)=>{calls.push(row);const card=new Element('session');card.cardId=row[0];parent.appendChild(card);});
    renders.push({dated:calendar.dataset.calendarDated,classes:grid.className,calls,
        columns:grid.children.map(column=>({date:column.dataset.calendarDate,empty:column.dataset.calendarEmpty==='true',
            cards:column.children.filter(node=>node.tag==='session').map(node=>node.cardId),
            labels:column.children.filter(node=>node.tag!=='session').map(node=>node.textContent)}))});
}
let currentIndex=null;
if(input.findCurrentWeek){
    scope.allParsedWeeks=weeks;scope.todayIso=input.today;scope.window.CoachCalendar=api;
    vm.runInContext(input.findCurrentWeek,scope);currentIndex=scope.findCurrentWeekIndex();
}
console.log(JSON.stringify({weeks,renders,currentIndex,unchanged:before===JSON.stringify(input.weeks),
    idempotent:JSON.stringify(weeks)===JSON.stringify(api.normalizeWeeks(weeks)),
    styleCount:document.head.children.length,css:document.head.children[0]?.textContent}));
'''


def row(day, name='Run', card_id=None):
    return {'dateStr': day, 'dayName': 'Intentionally stale weekday', 'workoutType': name,
            'cardId': card_id or 'session-'+day, 'distOrDuration': '30 minutes', 'target': 'Original target'}


@unittest.skipUnless(NODE, 'Node is optional for browser-library tests')
class CalendarLayoutTests(unittest.TestCase):
    def render(self, rows=None, *, weeks=None, timezone='UTC', **extra):
        payload = {'weeks': weeks if weeks is not None else [{'title': 'Saved week', 'rows': rows}], **extra}
        result = subprocess.run([NODE, '-e', RUNNER, str(ROOT/'dashboard_assets/calendar-layout.js'), json.dumps(payload)],
                                capture_output=True, text=True, check=True, env=dict(os.environ, TZ=timezone))
        output = json.loads(result.stdout)
        self.assertTrue(output['unchanged'])
        self.assertTrue(output['idempotent'])
        self.assertEqual(output['styleCount'], 1)
        return output

    def test_friday_start_has_four_empty_columns_without_extra_sessions(self):
        result = self.render([row('2026-11-06'), row('2026-11-07'), row('2026-11-08')])
        rendered = result['renders'][0]
        self.assertEqual([column['date'] for column in rendered['columns']], [f'2026-11-{day:02}' for day in range(2, 9)])
        self.assertEqual([column['empty'] for column in rendered['columns']], [True]*4+[False]*3)
        self.assertEqual([call[1] for call in rendered['calls']], ['Friday', 'Saturday', 'Sunday'])
        self.assertEqual([call[0] for call in rendered['calls']], ['session-2026-11-06', 'session-2026-11-07', 'session-2026-11-08'])
        self.assertEqual(rendered['columns'][0]['labels'], ['Monday', '2026-11-02', 'No session planned'])
        self.assertEqual(rendered['columns'][0]['cards'], [])

    def test_middle_trailing_gaps_and_multiple_sessions_preserve_real_rest(self):
        result = self.render([row('2026-11-05', 'Strength', 'strength'), row('2026-11-02', 'Run', 'run'),
                              row('2026-11-05', 'Easy run', 'easy'), row('2026-11-06', 'Rest', 'rest')])
        columns = result['renders'][0]['columns']
        self.assertEqual([column['cards'] for column in columns], [['run'], [], [], ['strength', 'easy'], ['rest'], [], []])
        self.assertFalse(columns[4]['empty'])
        self.assertEqual([call[3] for call in result['renders'][0]['calls']], ['Run', 'Strength', 'Easy run', 'Rest'])
        self.assertEqual(sum(len(column['cards']) for column in columns), 4)

    def test_legacy_table_spanning_year_and_month_boundaries_splits_actual_weeks(self):
        result = self.render([row('2027-01-04'), row('2026-12-31'), row('2027-01-01'), row('2027-01-03')])
        self.assertEqual([week['weekStart'] for week in result['weeks']], ['2026-12-28', '2027-01-04'])
        self.assertEqual([week['title'] for week in result['weeks']], ['Week of 2026-12-28', 'Week of 2027-01-04'])
        self.assertEqual([column['cards'] for column in result['renders'][0]['columns']],
                         [[], [], [], ['session-2026-12-31'], ['session-2027-01-01'], [], ['session-2027-01-03']])
        self.assertEqual(result['renders'][1]['columns'][0]['cards'], ['session-2027-01-04'])
        month = self.render([row('2026-10-31'), row('2026-11-01'), row('2026-11-02')])
        self.assertEqual([week['weekStart'] for week in month['weeks']], ['2026-10-26', '2026-11-02'])

    def test_utc_calendar_arithmetic_is_independent_of_browser_timezone_and_dst(self):
        fixtures = [row('2026-03-08'), row('2026-03-09'), row('2026-10-25'), row('2026-10-26')]
        expected = self.render(fixtures)
        for timezone in ('America/Los_Angeles', 'Europe/London', 'Pacific/Kiritimati'):
            with self.subTest(timezone=timezone):
                self.assertEqual(self.render(fixtures, timezone=timezone), expected)

    def test_undated_invalid_and_duplicate_tables_never_lose_sessions(self):
        result = self.render(weeks=[
            {'title': 'First table', 'rows': [row('2026-11-06', card_id='one'), row('', card_id='undated')]},
            {'title': 'Second table', 'rows': [row('2026-11-06', card_id='two'), row('2026-02-30', card_id='invalid')]},
        ])
        self.assertEqual(result['renders'][0]['columns'][4]['cards'], ['one', 'two'])
        self.assertEqual([week['weekStart'] for week in result['weeks']], ['2026-11-02', None, None])
        self.assertTrue(all(week['title'].startswith('Undated sessions') for week in result['weeks'][1:]))
        self.assertEqual([render['dated'] for render in result['renders']], ['true', 'false', 'false'])
        self.assertEqual([call[0] for render in result['renders'] for call in render['calls']], ['one', 'two', 'undated', 'invalid'])

    def test_generated_dashboard_selects_current_partial_week_without_today_or_monday_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root/'training_plan.md'
            plan.write_text('| Day | Date | Workout | Amount | Target |\n|---|---|---|---|---|\n|Friday|2026-11-06|Run|30 min|Easy effort|', encoding='utf-8')
            before = plan.read_bytes()
            html = build_dashboard(root/'missing.sqlite', plan, root/'dashboard.html', today=date(2026, 11, 4)).read_text(encoding='utf-8')
            self.assertEqual(plan.read_bytes(), before)
            self.assertFalse((root/'missing.sqlite').exists())
            self.assertIn('id="coach-calendar-layout"', html)
            self.assertIn('renderSelectedWeek.calendarAligned = true', html)
            function = re.search(r'function findCurrentWeekIndex\(\) \{.*?\n        \}', html, re.S)[0]
            result = self.render(weeks=[{'title': 'Older', 'rows': [row('2026-10-30')]},
                                        {'title': 'Partial current', 'rows': [row('2026-11-06')]}],
                                 today='2026-11-04', findCurrentWeek=function)
            self.assertEqual(result['currentIndex'], 1)
            self.assertIn('@container (min-width:640px)', result['css'])
            self.assertIn('@container (min-width:1120px)', result['css'])
            self.assertIn('grid-template-columns:repeat(7,minmax(0,1fr))', result['css'])


if __name__ == '__main__':
    unittest.main()
