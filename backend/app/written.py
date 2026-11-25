"""Deterministic written output. Routing never depends on the entries present."""
import hashlib
import json
from .adapters import DomainError
from .contracts import KnowledgeEntry, ReviewCase, RejectPayload
from .knowledge_templates import render


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


class WrittenResponseRenderer:
    def render(self, case: ReviewCase, entries, version: str):
        if case.scenario_id=='demo.no_brand' and case.slots.get('brand_name')!='无品牌':
            raise DomainError('WRITTEN_FACT_CONFLICT','无品牌场景与申请单品牌事实不一致',409)
        candidates=[e for e in entries if e.scenario_id==case.scenario_id and e.knowledge_version==version
                    and e.channel=='jingmai' and e.kind in ('scenario','script')
                    and e.status=='approved' and not e.issue_codes and 'synthetic' in e.scope]
        if not candidates:
            raise DomainError('WRITTEN_UNAVAILABLE','缺少适用的已审核书面模板',409)
        try:
            rendered=[render(e,case) for e in candidates]
        except ValueError as exc:
            raise DomainError('WRITTEN_SLOTS_MISSING','书面模板缺少可信槽位或含未解决占位符',409) from exc
        if len({e.approved_text for e in rendered})!=1:
            raise DomainError('WRITTEN_CONFLICT','适用书面模板存在冲突，需人工复核',409)
        if any(marker in (e.approved_text or '') for e in rendered for marker in ('评审批注','待审核','是否统一','暂未修改')):
            raise DomainError('WRITTEN_INTERNAL_NOTE','书面正文含未解决的内部评审内容',409)
        return sorted(rendered,key=lambda e:str(e.entry_id))[0]

    def payload(self,case,entries,version):
        entry=self.render(case,entries,version)
        return RejectPayload(type='reject_application',reason_codes=[case.scenario_id],
                             written_text=entry.approved_text,knowledge_entry_ids=[entry.entry_id])
