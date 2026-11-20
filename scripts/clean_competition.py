"""Build a private review dataset from a hash-checked competition workbook pair."""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from convert_private_sources import MAIN, convert


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
            scenarios.append({**record, 'id': f'pop-v1-{row}', 'taxonomy': list(map(normal, item['classification'])),
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

    # The special-scenarios workbook ends this answer in the middle of a condition.
    for pair in qa:
        if pair['source_ref']['sheet'] == '特殊场景' and pair['source_ref']['row'] == 13:
            pair['status'] = 'quarantined'
            issues.append({'source_ref': pair['source_ref'], 'code': 'incomplete_answer'})
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
        ('authorization_chain', [22, 52], [50], 26),
    ]:
        mappings.append({
            'scenario_id': 'demo.' + name, 'status': 'draft',
            'routing_sources': [s['source_ref'] for s in scenarios if s['source_ref']['row'] in main_rows],
            'written_candidates': [s['source_ref'] for s in scripts if s['style'] == 'written'
                                   and s['source_ref']['row'] in written_rows],
            'phone_candidates': [s['source_ref'] for s in scripts if s['source_ref']['sheet'] == '特殊场景'
                                 and s['source_ref']['row'] == phone_row],
        })
    return {'version': 'pop-v1-clean-1', 'mappings': mappings, 'source_manifest': package['source_manifest'],
            'principles': principles, 'scenarios': scenarios, 'samples': samples,
            'scripts': scripts, 'qa': qa, 'internal_review': internal, 'issues': issues,
            'counts': dict(package['counts'], scripts=len(scripts), qa=len(qa),
                           issues_by_code=dict(Counter(issue['code'] for issue in issues)))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='+')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    dest = args.output.resolve()
    root = Path(__file__).resolve().parents[1]
    if dest.is_relative_to(root) and not dest.is_relative_to(root / 'private-evidence'):
        parser.error('Use private-evidence/ or a directory outside the checkout')
    result = clean(convert(args.inputs))
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if dest.exists():
        if dest.read_text(encoding='utf-8') != content:
            parser.error('Destination contains a different dataset. Choose a new output filename.')
    else:
        with dest.open('x', encoding='utf-8') as out:
            out.write(content)
    print(result['counts'])
