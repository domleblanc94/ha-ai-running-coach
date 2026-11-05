"""Inspect coaching responses offline; --live is an explicit, separately capped opt-in.

Protocol checks and wording flags are not a judgment of training suitability.
Live evaluation never applies a plan or writes the production database.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing

from planning_engine import validate_review

CASES_FILE=Path(__file__).parent/'eval_cases.json'


def load_cases(path=CASES_FILE):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def assess_response(case,response):
    """Machine-verifiable schema/identity checks, with explicit human review prompts."""
    result={'case_id':case['case_id'],'schema_pass':False,'manual_review_required':True,
            'manual_review_prompts':case.get('expected_focus',[]),'wording_flags':[]}
    try:
        payload=json.loads(response) if isinstance(response,str) else response
        validate_review(payload,case['evidence'],case['plan'],case['evidence']['today'],case['profile'])
        result['schema_pass']=True
        result['structured_explanation_present']=all(k in payload for k in ('assessment','weekly_priorities','next_review_trigger'))
        text=json.dumps(payload,ensure_ascii=False)
        for label,pattern in [('punitive_language',r'non-negotiable|severe failure|disregard of instructions'),
                              ('unsupported_causal_claim_to_review',r'proves? (?:poor fitness|overtraining)|prevents? (?:all )?aerobic adaptations?|fast.twitch.*(?:must|only).*walk')]:
            if re.search(pattern,text,re.I):result['wording_flags'].append(label)
        result['trajectory']=payload['trajectory']
        result['interpretation']='Schema valid does not establish coaching quality. Review evidence use, uncertainty, athlete request and workout suitability manually.'
    except (ValueError,TypeError,KeyError) as exc:
        result['error']=str(exc)
    return result


def saved_request_case(db_path,request_id):
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        row=conn.execute('SELECT input_snapshot_json,response_text FROM coach_api_requests WHERE id=?',(request_id,)).fetchone()
    if not row:raise ValueError('Saved API request not found.')
    snapshot=json.loads(row[0]);snapshot.update(case_id='saved-request-'+str(request_id),expected_focus=[
        'Does the response follow this athlete request and configured goals?',
        'Are physiological claims supported and uncertainty explained?',
        'Are proposed workouts appropriate? This requires human coaching judgment.'])
    return snapshot,row[1] or ''


def offline_report(cases,responses):
    return {'mode':'offline','api_attempts':0,'evaluated_responses':sum(case['case_id'] in responses for case in cases),
            'results':[assess_response(case,responses[case['case_id']]) for case in cases if case['case_id'] in responses],
            'missing_responses':[case['case_id'] for case in cases if case['case_id'] not in responses],
            'unused_response_ids':sorted(set(responses)-{case['case_id'] for case in cases}),
            'note':'No model was called. Passing protocol checks does not certify training advice.'}


def run_live(cases,output,max_calls=1,model=None,max_tokens=2048):
    if not 1<=max_calls<=5 or not 256<=max_tokens<=4096:
        raise ValueError('Live evaluation permits 1-5 calls and 256-4096 output tokens per call.')
    from ai_coach import api_client,limits,make_prompt,PROMPT_VERSION,review_identity
    from migrate_plan import atomic_write
    model=model or limits()[0][0]
    report={'mode':'live','request_kind':'evaluation','api_attempts':0,'max_calls':max_calls,'requests':[],'results':[],
            'note':'Evaluation only: no production plan/database changes. Token usage is measured when the provider returns it; monetary cost is not inferred.'}
    client=api_client()
    for case in cases[:max_calls]:
        prompt=make_prompt(case['profile'],case['plan'],case['evidence'],case['question'])
        record={'case_id':case['case_id'],'model':model,'provider':review_identity()['provider'],'prompt_version':PROMPT_VERSION,'status':'started',
                'started_at':datetime.now(timezone.utc).isoformat(),'input_chars':len(prompt),'max_output_tokens':max_tokens}
        report['requests'].append(record);report['api_attempts']+=1
        atomic_write(output,json.dumps(report,indent=2,ensure_ascii=False))
        try:
            config={'response_mime_type':'application/json','temperature':0.2,'max_output_tokens':max_tokens}
            response=client.models.generate_content(model=model,contents=prompt,config=config)
            record['response_text']=response.text
            usage=getattr(response,'usage_metadata',None)
            record['usage']=usage.model_dump(mode='json') if usage and hasattr(usage,'model_dump') else {}
            result=assess_response(case,response.text);report['results'].append(result)
            record['status']='received' if result['schema_pass'] else 'invalid'
        except Exception as exc:
            record.update(status='failed',error=str(exc)[:1000])
        record['completed_at']=datetime.now(timezone.utc).isoformat()
        atomic_write(output,json.dumps(report,indent=2,ensure_ascii=False))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=CASES_FILE)
    parser.add_argument('--case',action='append',dest='case_ids')
    parser.add_argument('--responses',type=Path,help='JSON mapping case IDs to response objects, or directory of CASE_ID.json files')
    parser.add_argument('--db',type=Path,help='Read a saved production response without writing its database')
    parser.add_argument('--request-id',type=int)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--live',action='store_true',help='Explicitly permits model API calls against anonymized cases only')
    parser.add_argument('--max-calls',type=int,default=1)
    parser.add_argument('--model')
    parser.add_argument('--max-output-tokens',type=int,default=2048)
    args=parser.parse_args(argv)
    if args.live and (args.db or args.responses):parser.error('--live cannot be combined with saved private responses/database.')
    if args.live and not args.output:parser.error('--live requires --output to preserve the request/usage record.')
    if bool(args.db)!=(args.request_id is not None):parser.error('--db and --request-id must be supplied together.')
    if args.db:
        case,response=saved_request_case(args.db,args.request_id)
        report=offline_report([case],{case['case_id']:response})
    else:
        cases=load_cases(args.cases)
        if args.case_ids:
            known={case['case_id'] for case in cases}
            if set(args.case_ids)-known:parser.error('Unknown case ID.')
            cases=[case for case in cases if case['case_id'] in args.case_ids]
        if args.live:report=run_live(cases,args.output,args.max_calls,args.model,args.max_output_tokens)
        else:
            responses={}
            if args.responses:
                if args.responses.is_dir():
                    responses={p.stem:p.read_text(encoding='utf-8') for p in args.responses.glob('*.json')}
                else:responses=json.loads(args.responses.read_text(encoding='utf-8'))
            report=offline_report(cases,responses)
            if not responses:report['cases']=[{'case_id':case['case_id'],'review_prompts':case['expected_focus']} for case in cases]
    if args.output and not args.live:
        from migrate_plan import atomic_write
        atomic_write(args.output,json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 1 if any(not result['schema_pass'] for result in report['results']) or any(r['status']=='failed' for r in report.get('requests',[])) else 0


if __name__=='__main__':raise SystemExit(main())
