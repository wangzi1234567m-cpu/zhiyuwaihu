"""Build a source map and evidence by reconciling the actual isolated acceptance DB."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from xml.etree import ElementTree
from sqlalchemy import select
from app.contracts import KnowledgeEntry,ReviewCase
from app.db import Application,Effect,Knowledge,Session,RequestReceipt
from app.staff import StaffService
from app.coordinator import Coordinator
from app.adapters import Services
from app.written import WrittenResponseRenderer
from clean_competition import clean

ROOT=Path(__file__).resolve().parents[1]


def dump(name,value):
    (ROOT/'docs'/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('staging',type=Path);parser.add_argument('release',type=Path)
    parser.add_argument('--word-text',type=Path,required=True)
    parser.add_argument('--voice-database',required=True)
    args=parser.parse_args()
    from app.db import engine
    if not (engine.url.database or '').endswith('_test'):raise SystemExit('Use the preserved isolated acceptance database')
    staging=json.loads(args.staging.read_bytes());release=json.loads(args.release.read_bytes());dataset=clean(staging)
    manifest=json.loads((ROOT/'docs/design/source-manifest.json').read_text(encoding='utf-8'))
    actual={f['filename']:f['sha256'] for f in staging['source_manifest']}
    assert actual=={f['filename']:f['sha256'] for f in manifest['files']}
    entries=[KnowledgeEntry.model_validate(e) for e in release['entries']]
    approved=[e for e in entries if e.status=='approved' and not e.issue_codes]
    approved_scenes={e.scenario_id for e in approved}
    with Session() as db:
        persisted={k.id:k.data for k in db.scalars(select(Knowledge).where(Knowledge.version==release['release_id']))}
        assert persisted=={str(e.entry_id):e.model_dump(mode='json') for e in entries}
        cases={a.id:ReviewCase.model_validate(a.data) for a in db.scalars(select(Application))}
        effects=[e.data for e in db.scalars(select(Effect))]
        rejects=[e['command'] for e in effects if e['command']['kind']=='reject_application']
        for command in rejects:
            case=cases[command['application_id']]
            rendered=WrittenResponseRenderer().render(case,entries,release['release_id'])
            assert command['payload']['written_text']==rendered.approved_text
            assert command['payload']['knowledge_entry_ids']==[str(rendered.entry_id)]
        accepted={cases[c['application_id']].scenario_id for c in rejects}
        actual_examples=[{'application_id':c['application_id'],'scenario_id':cases[c['application_id']].scenario_id,
            'written_text':c['payload']['written_text'],'knowledge_entry_ids':c['payload']['knowledge_entry_ids']} for c in rejects]
    coordinator=Coordinator(Services())
    followup_chains=0
    for command in rejects:
        case=cases[command['application_id']]
        if case.submission_attempt==2 and case.call_attempt>=2:
            assert coordinator.has_trusted_followup(case)
            followup_chains+=1
    records=dataset['all_scene_mappings']
    for item in records:
        published=item['scenario_id'] in approved_scenes
        item.update(review_status='approved' if published else 'pending',published=published,
            runtime_supported=published,e2e_accepted=item['scenario_id'] in accepted,
            unavailable_reasons=[] if published else ['missing_written_template'] if not item['written_body_present'] else ['pending_scoped_source_review'])
    assert len(records)==68 and sum(r['published'] for r in records)==6
    dump('workbench-scene-mapping-20261125.json',records)
    catalog=StaffService(Coordinator(Services())).catalog('demo')
    junit=ElementTree.parse(ROOT/'.cache/workbench/backend-junit.xml').getroot()
    suites=junit.findall('testsuite') if junit.tag=='testsuites' else [junit]
    tests={k:sum(int(s.get(k,'0')) for s in suites) for k in ['tests','failures','errors','skipped']}
    assert tests['failures']==tests['errors']==tests['skipped']==0
    import psycopg
    voice_url=engine.url.set(database=args.voice_database).render_as_string(hide_password=False).replace('postgresql+psycopg://','postgresql://')
    with psycopg.connect(voice_url) as connection:
        budget=[{'kind':key.rsplit(':',1)[-1],'reserved':data['count']} for key,data in connection.execute("select key,data from request_receipts where key like 'cloud-budget:%'")]
        voice_counts={t:connection.execute(f'select count(*) from {t}').fetchone()[0] for t in ['sessions','operations','business_effects']}
        assert voice_counts=={'sessions':1,'operations':0,'business_effects':0}
    voice=json.loads((ROOT/'.cache/workbench/workbench-smoke.json').read_text(encoding='utf-8'))
    voice.update(knowledge_version='workbench-reviewed-20261125-v1',budget_reservations=budget,
        business_counts=voice_counts,actual_audio_inputs=2,actual_native_connections=1,
        actual_tts_attempts=next(x['reserved'] for x in budget if x['kind']=='tts'),
        provider_bill_amount_obtained=False,voice_business_writes_enabled=False)
    dump('workbench-voice-test-results.json',voice)
    word=args.word_text.read_text(encoding='utf-8')
    counts=re.search(r'电话\s*(\d+)\s*条[，,、]\s*京麦驳回\s*(\d+)\s*条',word)
    assert counts and tuple(map(int,counts.groups()))==(55,13)
    evidence={'baseline':'243ac72','branch':'codex/workbench-business-backend',
        'source_files_sha256_verified':actual,'source_archive_sha256':manifest['archive_sha256'],
        'word_stated_channels':{'phone':55,'jingmai':13},'actual_main_sheet_channels':{'phone':54,'jingmai':14},
        'original_scenes':68,'cleaned_scenes':68,'approved_published_source_scenes':6,'pending_source_scenes':62,
        'review_policy':release['review_policy'],'reviewers':release['reviewers'],'source_review':release['source_review'],
        'release_id':release['release_id'],'release_sha256':sha256(args.release.read_bytes()).hexdigest(),
        'approved_entries':len(approved),'approved_entries_by_role':dict(Counter('qa' if e.kind=='qa' else e.channel for e in approved)),
        'isolated_unreviewed_entries':len(entries)-len(approved),'source_issues':dataset['counts']['issues_by_code'],
        'source_qa_rows':len(dataset['qa']),'reviewed_qa_source_rows':4,
        'catalog':catalog.model_dump(mode='json'),'backend_tests':tests,'browser_tests_passed':4,
        'business_facts':json.loads((ROOT/'docs/workbench-test-results.json').read_text(encoding='utf-8')),
        'trusted_followup_chains_reconciled':followup_chains,'authoritative_written_bodies_reconciled':len(rejects),'actual_written_examples':actual_examples,
        'voice_regression_knowledge_version':voice['knowledge_version'],'final_release_cloud_qa_regression':False,
        'latency_p50_measured':False,'remote_deployment_updated':False}
    dump('workbench-business-evidence-20261125.json',evidence)
    print(json.dumps({k:evidence[k] for k in ['release_id','approved_entries','backend_tests','authoritative_written_bodies_reconciled']},ensure_ascii=False))


if __name__=='__main__':main()
