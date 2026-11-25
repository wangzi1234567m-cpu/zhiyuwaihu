"""Generate the private, unapproved three-scenario manifest for joint review."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid5

from clean_competition import clean
from convert_private_sources import save_private_json
from app.knowledge_templates import REQUIRED_SLOTS


REVIEW_SHA256 = '837d7ff535dc4b140b975409a32f7bb395823e0f49d0b6ea8278f858df91563a'


def draft(staging_bytes, release, opinion=None):
    data = clean(json.loads(staging_bytes))
    entries = []
    for mapping in data['mappings']:
        refs = mapping['routing_sources'] + mapping['phone_candidates'] + mapping['written_candidates']
        scenario = mapping['scenario_id']
        entries.append({
            'entry_id': str(uuid5(UUID('1216c23d-4cf6-4aa1-ba77-60bc94973502'), release + '/' + scenario)),
            'knowledge_version': release, 'scenario_id': scenario, 'kind': 'scenario',
            'scope': ['synthetic'], 'channel': 'phone', 'status': 'draft',
            'required_slots': list(REQUIRED_SLOTS[scenario]), 'approved_text': None,
            'source_refs': refs, 'issue_codes': ['pending_release_approval'], 'reviewer': None,
        })
    manifest = {'schema_version': '0.3.0', 'release_id': release, 'staging_sha256': sha256(staging_bytes).hexdigest(),
            'reviewers': [], 'entries': entries}

    if opinion is not None:
        if sha256(opinion).hexdigest() != REVIEW_SHA256:
            raise ValueError('Unknown source opinion; review the new version first')
        templates = [line.split('：', 1)[1] for line in opinion.decode('utf-8-sig').splitlines()
                     if line.startswith('核验后的说明/书面正文：')]
        for entry, template in zip(entries, templates, strict=True):
            entry['approved_text'] = template
        manifest['source_review'] = {'sha256': REVIEW_SHA256, 'date': '2026-11-21',
                                     'reviewer': 'Codex（受委托核对原件）', 'scope': '三项原件映射及模板'}
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('staging', type=Path)
    parser.add_argument('--source-opinion', type=Path, help='Private delegated mapping review dated 2026-11-21')
    parser.add_argument('--all-scenes', action='store_true')
    parser.add_argument('--delegated-review', action='store_true',help='Only explicitly delegated, inspected source cells; records the real single reviewer')
    parser.add_argument('--release', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.all_scenes:
        from workbench_knowledge import candidates
        raw=args.staging.read_bytes()
        result=candidates(clean(json.loads(raw)),args.release,delegated_review=args.delegated_review,staging_sha256=sha256(raw).hexdigest())
    else:
        if args.delegated_review:parser.error('--delegated-review requires --all-scenes')
        result=draft(args.staging.read_bytes(),args.release,args.source_opinion.read_bytes() if args.source_opinion else None)
    save_private_json(args.output,result)
