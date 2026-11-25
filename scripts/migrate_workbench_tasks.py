"""Explicit, additive metadata migration for known synthetic tasks. Dry run by default.

Never overwrites existing facts, slots, knowledge versions or session history.
"""
import argparse
import hashlib
import json
from sqlalchemy import select
from app.db import Application,Session,RequestReceipt

KNOWN={
    'demo.authorization_missing':('phone','授权书内容不完整'),
    'demo.authorization_expired':('phone','授权时间到期'),
    'demo.authorization_chain':('phone','授权链路不完整'),
    'demo.unanswered_authorization':('phone','授权书内容不完整（联系分流演示）'),
    'demo.no_brand':('jingmai','无品牌申请'),
    'demo.qualification_fraud':('jingmai','资质造假'),
    'demo.governance_disabled':('jingmai','治理停用'),
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner',default='demo')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();changes=[]
    with Session.begin() as db:
        for row in db.scalars(select(Application).where(Application.owner==args.owner).with_for_update()):
            original=row.data
            if original.get('synthetic',True) is not True or original.get('scenario_id') not in KNOWN:continue
            route,name=KNOWN[original['scenario_id']]
            proposed={**original}
            for field,value in {'default_route':route,'scene_name':name,'business_category':'历史合成资审任务'}.items():
                if field not in proposed:proposed[field]=value
            fields=sorted(set(proposed)-set(original))
            if not fields:continue
            changes.append({'application_id':row.id,'added_fields':fields})
            if args.apply:
                row.data=proposed
                key=f'workbench-metadata-v1:{row.id}'
                if db.get(RequestReceipt,key) is None:
                    db.add(RequestReceipt(key=key,payload_hash=hashlib.sha256(json.dumps(proposed,sort_keys=True).encode()).hexdigest(),
                        data={'added_fields':fields,'case_revision':original['case_revision'],'no_existing_facts_overwritten':True}))
    print(json.dumps({'applied':args.apply,'changes':changes},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
