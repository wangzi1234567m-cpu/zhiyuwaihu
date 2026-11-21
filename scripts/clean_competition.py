"""Build a private review dataset from a hash-checked competition workbook pair."""
import argparse
import json
from hashlib import sha256
import re
from collections import Counter, defaultdict
from pathlib import Path

from convert_private_sources import MAIN, convert, save_private_json


SCRIPT_SHEET = '驳回话术（开头语+驳回话术+结束语）'
# Coordinates apply only to workbook versions accepted by the source manifest.
QUARANTINE = {
    ('高频QA', 8, 'C'): 'contradictory_seal_answer',
    ('高频QA', 11, 'C'): 'ambiguous_validity_scope',
    (SCRIPT_SHEET, 8, 'B'): 'incomplete_authorization_object',
    ('特殊场景', 6, 'B'): 'incomplete_authorization_object',
    ('特殊场景', 13, 'D'): 'incomplete_answer',
}


def stable_id(kind, ref, path):
    identity = [kind, ref['file_sha256'], ref['sheet'], path, ref['row']]
    return kind + '-' + sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def normal(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def clean(package):
    scenarios, scripts, qa, internal, issues = [], [], [], [], []
    samples = []
    for item in package['rows']:
        ref = item['source_ref']
        sheet, row = ref['sheet'], ref['row']
        cells = {key: normal(value) for key, value in item['raw_cells'].items()}
        record = {'source_ref': ref, 'status': 'draft'}
        for code in item.get('issue_codes', []):
            issues.append({'source_ref': ref, 'code': code})
        if item.get('internal_comments'):
            internal.append({**record, 'comments': item['internal_comments']})
        if sheet == MAIN and row == 2:
            principles = {**record, 'text': cells['A']}
        elif sheet == MAIN:
            template = cells.get('E', '')
            scenarios.append({**record, 'id': stable_id('scenario', ref, item['classification']), 'taxonomy': list(map(normal, item['classification'])),
                              'channel': item['channel'], 'written_template': template or None})
            if not template:
                issues.append({'source_ref': ref, 'code': 'missing_written_template'})
        elif sheet == '工作表1' and row > 1:
            internal.append({**record, 'review_cells': cells})
        elif sheet in ('京麦驳回场景', '电话场景') and row >= 14:
            samples.append({**record, 'taxonomy': list(map(normal, item['classification'])),
                            'channel': {'电话': 'phone', '京麦驳回': 'jingmai'}.get(cells.get('E')),
                            'template': cells.get('D')})
        elif sheet.startswith('驳回话术') and row > 1:
            scripts.append({**record, 'style': 'phone' if row <= 10 else 'written',
                            'label': cells.get('A'), 'text': cells['B']})
        elif sheet == '高频QA' and row > 1:
            qa.append({**record, 'topic': cells.get('A'), 'question': cells['B'], 'answer': cells.get('C')})
        elif sheet == '特殊场景' and row > 1:
            question = cells.get('C', '')
            marker = question == '京麦驳回'
            scripts.append({**record, 'style': 'phone', 'text': cells['B'],
                            'routing_note': 'jingmai' if marker else None})
            if question and not marker:
                qa.append({**record, 'question': question, 'answer': cells.get('D') or None})
                if not cells.get('D'):
                    issues.append({'source_ref': ref, 'code': 'missing_qa_answer'})

    for group, kind in ((scripts, 'script'), (qa, 'qa')):
        for entry in group:
            ref = entry['source_ref']
            entry['id'] = stable_id(kind, ref, entry.get('label') or entry.get('topic'))
            column = 'B' if kind == 'script' else ('D' if ref['sheet'] == '特殊场景' else 'C')
            code = QUARANTINE.get((ref['sheet'], ref['row'], column))
            if code:
                entry['status'] = 'quarantined'
                issues.append({'source_ref': {**ref, 'columns': [column]}, 'code': code,
                               'entry_id': entry['id']})
            if ref['sheet'] == SCRIPT_SHEET and ref['row'] == 50:
                entry['scope'] = ['全球购医药']
    for scenario in scenarios:
        template = scenario['written_template'] or ''
        # Keep positional blanks separate: different slots can occur in one template.
        scenario['placeholder_tokens'] = re.findall(r'【[^】]*】', template)
    by_path = defaultdict(list)
    for scenario in scenarios:
        by_path[tuple(scenario['taxonomy'])].append(scenario)
    for sample in samples:
        candidates = by_path[tuple(sample['taxonomy'])]
        sample['candidate_ids'] = [c['id'] for c in candidates]
        if not candidates or any(c['channel'] != sample['channel'] for c in candidates):
            sample['status'] = 'quarantined'
            issues.append({'source_ref': sample['source_ref'], 'code': 'sample_requires_review'})
    # Candidate links apply only to the two hashed V1 workbooks checked by convert().
    # The brand and intake branches retain distinct routing rows and written variants.
    mappings = []
    for name, main_rows, written_rows, phone_row in [
        ('authorization_missing', [17, 50], [21, 51], 20),
        ('authorization_expired', [21, 51], [26, 52], 25),
        ('authorization_chain', [22, 52], [27], 26),
    ]:
        mappings.append({
            'scenario_id': 'demo.' + name, 'status': 'draft',
            'routing_sources': [{**s['source_ref'], 'columns': ['D', 'E']} for s in scenarios if s['source_ref']['row'] in main_rows],
            'written_candidates': [{**s['source_ref'], 'columns': ['B']} for s in scripts if s['style'] == 'written'
                                   and s['source_ref']['row'] in written_rows],
            'phone_candidates': [{**s['source_ref'], 'columns': ['B']} for s in scripts if s['source_ref']['sheet'] == '特殊场景'
                                 and s['source_ref']['row'] == phone_row],
        })
    return {'version': 'pop-v1-clean-2', 'mappings': mappings, 'source_manifest': package['source_manifest'],
            'principles': principles, 'scenarios': scenarios, 'samples': samples,
            'scripts': scripts, 'qa': qa, 'internal_review': internal, 'issues': issues,
            'counts': dict(package['counts'], scripts=len(scripts), qa=len(qa),
                           issues_by_code=dict(Counter(issue['code'] for issue in issues)))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='+')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = clean(convert(args.inputs))
    save_private_json(args.output, result)
    print(result['counts'])
