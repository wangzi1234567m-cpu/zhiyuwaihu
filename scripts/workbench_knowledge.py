"""Incremental source mapping and delegated review on the existing clean dataset.

Only explicitly inspected cells are approved. All other rows remain candidates.
"""
from hashlib import sha256
from uuid import UUID,uuid5
import re

REVIEWER='Codex（用户委托核对原件，2026-11-25）'
NAMESPACE=UUID('90942808-a5ac-4e4d-a6d0-1d3a293153ce')
PRIORITIES={17:('demo.authorization_missing',20),21:('demo.authorization_expired',25),22:('demo.authorization_chain',26),
            15:('demo.qualification_fraud',None),16:('demo.governance_disabled',None),24:('demo.no_brand',None)}


def mappings(clean):
    return [{'original_id':s['id'],'scenario_id':PRIORITIES.get(s['source_ref']['row'],(s['id'],None))[0],
             'row':s['source_ref']['row'],'taxonomy':s['taxonomy'],'default_route':s['channel'],
             'source_ref':s['source_ref'],'raw':True,'cleaned':True,'review_status':'pending',
             'written_body_present':bool(s['written_template']),'placeholder_count':len(s['placeholder_tokens']),
             'runtime_supported':False,'e2e_accepted':False} for s in clean['scenarios']]


def candidates(clean,version,*,delegated_review=False,staging_sha256=''):
    rows={s['source_ref']['row']:s for s in clean['scenarios']}
    entries=[]
    for s in clean['scenarios']:
        row=s['source_ref']['row'];scenario,phone_row=PRIORITIES.get(row,(s['id'],None))
        for channel in ('phone','jingmai'):
            refs=[{**s['source_ref'],'columns':['D','E'] if s['written_template'] else ['D']}]
            body=None;required=[]
            if row in PRIORITIES and channel=='jingmai':
                body=s['written_template'];required=['brand_name'] if row!=24 else []
                if row==17:
                    required+=['missing_items','missing_document']
                    body=body.replace('【】','{brand_name}',1).replace('【】','{missing_items}',1).replace('【】','{missing_document}',1)
                elif row==21:
                    required+=['authorization_expiry'];body=body.replace('【】','{brand_name}').replace('【授权时间】','{authorization_expiry}').replace('【重新开具授权书】','重新开具授权书')
                elif row==22:
                    required+=['authorization_from','authorization_to'];body=body.replace('【】','{brand_name}',1).replace('【】','{authorization_from}',1).replace('【】','{authorization_to}',1)
                elif row==15:
                    required+=['qualification_name','invalid_fact']
                    body=body.replace('【】','{brand_name}').replace('发票/合同/流水/信息存在修改信息','{qualification_name}存在{invalid_fact}')
                else:body=body.replace('【】','{brand_name}')
                body='【模拟演示】'+body.replace('我是京东资质审核专员','我是模拟商户资审告知助手')
            elif phone_row and channel=='phone':
                script=next(x for x in clean['scripts'] if x['source_ref']['sheet']=='特殊场景' and x['source_ref']['row']==phone_row)
                body=script['text'].split('联系您是因为',1)[1].split('感谢您的接听',1)[0]
                body='联系您是因为'+body.replace('【XX】','{brand_name}',1)
                required=['company_name','brand_name']
                if row==17:required+=['missing_items','missing_document'];body=body.replace('【XX】','{missing_items}、{missing_document}')
                if row==21:required+=['authorization_expiry'];body=body.replace('【授权时间】','{authorization_expiry}')
                if row==22:
                    required+=['authorization_from','authorization_to'];body=body.replace('【XX】','{authorization_from}',1).replace('【XX】','{authorization_to}',1)
                refs.append({**script['source_ref'],'columns':['B']})
            if row==22:
                refs += [{**rows[52]['source_ref'],'columns':['D','E']}]
                for sheet,r in [('特殊场景',26),('驳回话术（开头语+驳回话术+结束语）',27)]:
                    script=next(x for x in clean['scripts'] if x['source_ref']['sheet']==sheet and x['source_ref']['row']==r)
                    refs.append({**script['source_ref'],'columns':['B']})
            # Legacy authorization validation requires the same trusted slot minimum.
            from app.knowledge_templates import REQUIRED_SLOTS
            required=sorted(set(required)|set(REQUIRED_SLOTS.get(scenario,())))
            approved=delegated_review and body is not None
            entries.append({'entry_id':str(uuid5(NAMESPACE,version+'/'+s['id']+'/'+channel)),
                'knowledge_version':version,'scenario_id':scenario,'kind':'script' if channel=='jingmai' else 'scenario',
                'taxonomy':s['taxonomy'],'scope':['synthetic'],'channel':channel,'required_slots':required,
                'approved_text':body,'source_refs':refs,'status':'approved' if approved else 'quarantined',
                'issue_codes':[] if approved else ['pending_scoped_source_review'] if s['written_template'] else ['missing_written_template'],
                'reviewer':REVIEWER if approved else None})
    # Independently reviewed QA: each topic has explicit source and applicability.
    qa_specs=[('高频QA',12,'qa.outcome',tuple(v[0] for k,v in PRIORITIES.items() if v[1])),
              ('高频QA',2,'qa.contact_upload',tuple(v[0] for k,v in PRIORITIES.items() if v[1])),
              ('特殊场景',24,'qa.fixed_template',tuple(v[0] for k,v in PRIORITIES.items() if v[1])),
              ('特殊场景',22,'qa.chain_standard',('demo.authorization_chain',))]
    for sheet,row,topic,scenarios in qa_specs:
        source=next(q for q in clean['qa'] if q['source_ref']['sheet']==sheet and q['source_ref']['row']==row)
        for scenario in scenarios:
            phone=next(e for e in entries if e['scenario_id']==scenario and e['channel']=='phone')
            entries.append({**phone,'entry_id':str(uuid5(NAMESPACE,version+'/'+scenario+'/'+topic)),
                'kind':'qa','taxonomy':[topic],'approved_text':source['answer'],
                'source_refs':[*phone['source_refs'],{**source['source_ref'],'columns':['C' if sheet=='高频QA' else 'D']}],
                'status':'approved' if delegated_review else 'quarantined',
                'issue_codes':[] if delegated_review else ['pending_scoped_source_review'],'reviewer':REVIEWER if delegated_review else None})
    return {'schema_version':'0.3.0','release_id':version,'staging_sha256':staging_sha256,
            'review_policy':'delegated_source_review' if delegated_review else 'two_reviewers',
            'reviewers':[REVIEWER] if delegated_review else [],
            'source_review':{'reviewer':REVIEWER,'date':'2026-11-25','authorization':'用户本轮明确委托原件核对及版本管理，不要求两位审核人',
                'scope':'场景汇总E15/E16/E17/E21/E22/E24；特殊场景B20/B25/B26；身份模拟化、槽位命名、完整缺项列表展开与结束语分段；高频QA C2/C12，特殊场景 D22/D24（按场景隔离）'},
            'entries':entries}
