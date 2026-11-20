"""Generate the private, unapproved three-scenario manifest for joint review."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid5

from clean_competition import clean


def draft(staging_bytes, release):
    data = clean(json.loads(staging_bytes))
    entries = []
    for mapping in data['mappings']:
        refs = mapping['routing_sources'] + mapping['phone_candidates'] + mapping['written_candidates']
        scenario = mapping['scenario_id']
        entries.append({
            'entry_id': str(uuid5(UUID('1216c23d-4cf6-4aa1-ba77-60bc94973502'), release + '/' + scenario)),
            'knowledge_version': release, 'scenario_id': scenario, 'kind': 'scenario',
            'scope': ['synthetic'], 'channel': 'phone', 'status': 'draft',
            'required_slots': ['company_name'], 'approved_text': None,
            'source_refs': refs, 'issue_codes': ['pending_joint_review'], 'reviewer': None,
        })
    return {'schema_version': '0.3.0', 'release_id': release, 'staging_sha256': sha256(staging_bytes).hexdigest(),
            'reviewers': [], 'entries': entries}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('staging', type=Path)
    parser.add_argument('--release', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    dest = args.output.resolve()
    if dest.is_relative_to(root) and not dest.is_relative_to(root / 'private-evidence'):
        parser.error('Use private-evidence/ or a directory outside the checkout')
    result = draft(args.staging.read_bytes(), args.release)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('x', encoding='utf8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
