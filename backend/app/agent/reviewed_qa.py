"""Scoped deterministic QA selection. Audit facts are never sourced from chat."""
import re

PATTERNS={
    'qa.contact_upload':r'(联系方式|联系信息|联系人信息).*(上传|提交|补充)|在哪.*上传.*联系',
    'qa.fixed_template':r'(固定|统一).{0,8}模板|模板.{0,10}(固定|统一|改不|不能改|无法改|没法改)',
    'qa.chain_standard':r'经销商|为什么.{0,8}授权|为什么.{0,8}链路',
}


def select_qa(knowledge,case,version,text,intents):
    selected=[]
    for entry in knowledge:
        if (entry.kind!='qa' or entry.channel!='phone' or entry.status!='approved' or entry.issue_codes
                or entry.scenario_id!=case.scenario_id or entry.knowledge_version!=version):continue
        topics=set(entry.taxonomy)
        matched=('qa.outcome' in topics and bool(set(intents)&{'ask_guarantee','ask_outcome'})) or any(
            topic in topics and re.search(pattern,text) for topic,pattern in PATTERNS.items())
        if matched:selected.append(entry)
    for topic in {t for e in selected for t in e.taxonomy}:
        if len({e.approved_text for e in selected if topic in e.taxonomy})>1:
            return [],True
    return selected,False
