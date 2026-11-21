import json
from hashlib import sha256
from uuid import uuid4

import pytest

from clean_competition import MAIN, QUARANTINE, SCRIPT_SHEET, clean
from convert_private_sources import save_private_json
from publish_reviewed_knowledge import validate


def staging():
    rows = []
    def add(sheet, row, cells, **fields):
        rows.append({'source_ref': {'file_sha256': 'a'*64, 'sheet': sheet, 'row': row,
                                    'columns': list(cells)}, 'raw_cells': cells, **fields})
    add(MAIN, 2, {'A': 'fixture'})
    for row in (17, 21, 22, 50, 51, 52):
        add(MAIN, row, {'D': '电话', 'E': 'fixture'}, classification=['mock'], channel='phone')
    for sheet, row, col in QUARANTINE:
        cells = {'B': 'synthetic', col: 'original'}
        if col == 'D':
            cells['C'] = 'fixture question'
        add(sheet, row, cells)
    for row in (21, 26, 27, 50, 51, 52):
        add(SCRIPT_SHEET, row, {'B': 'synthetic'})
    for row in (20, 25, 26):
        add('特殊场景', row, {'B': 'synthetic'})
    return {'schema_version': '0.3.0', 'source_manifest': [], 'rows': rows, 'counts': {}}


def test_private_output_safe_rerun(tmp_path):
    dest = tmp_path / 'new' / 'nested' / 'dataset.json'
    save_private_json(dest, {'data': '一'})
    before = dest.read_bytes()
    save_private_json(dest, {'data': '一'})
    with pytest.raises(ValueError, match='different data'):
        save_private_json(dest, {'data': '二'})
    assert before == dest.read_bytes()


def test_quarantine_stable_ids_and_general_chain():
    source = staging()
    result = clean(source)
    assert clean(source) == result
    entries = result['scenarios'] + result['scripts'] + result['qa']
    assert len({e['id'] for e in entries}) == len(entries)
    held = {e['id'] for e in entries if e['status'] == 'quarantined'}
    locations = {(i['source_ref']['sheet'], i['source_ref']['row'], i['source_ref']['columns'][0])
                 for i in result['issues'] if i.get('entry_id') in held}
    assert locations == set(QUARANTINE)
    chain = result['mappings'][-1]
    assert [r['row'] for r in chain['written_candidates']] == [27]
    assert [r['row'] for r in chain['phone_candidates']] == [26]
    assert [r['row'] for r in chain['routing_sources']] == [22, 52]
    for row in source['rows']:
        row['source_ref']['file_sha256'] = 'b'*64
    new = clean(source)
    assert {e['id'] for e in entries}.isdisjoint(e['id'] for group in ['scenarios','scripts','qa'] for e in new[group])


def test_publisher_refuses_quarantined_cell_even_if_marked_approved():
    raw = json.dumps(staging()).encode()
    ref = {'file_sha256': 'a'*64, 'sheet': '高频QA', 'row': 8, 'columns': ['C']}
    entry = dict(entry_id=str(uuid4()), knowledge_version='test', scenario_id='test', kind='qa',
                 scope=['synthetic'], channel='phone', required_slots=[], approved_text='synthetic',
                 status='approved', reviewer='test-a', source_refs=[ref], issue_codes=[])
    review = dict(schema_version='0.3.0', release_id='test', staging_sha256=sha256(raw).hexdigest(),
                  reviewers=['test-a','test-b'], entries=[entry])
    with pytest.raises(ValueError, match='quarantined'):
        validate(raw, review)
