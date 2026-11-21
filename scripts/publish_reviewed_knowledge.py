"""Validate and import an explicitly reviewed knowledge release using operator DB access."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from app.contracts import KnowledgeEntry
from clean_competition import QUARANTINE, SCRIPT_SHEET
from app.knowledge_templates import REQUIRED_SLOTS, check_template


def validate(staging_bytes, review):
    staging = json.loads(staging_bytes)
    version = review['release_id']
    if (staging['schema_version'] != '0.3.0' or review['schema_version'] != '0.3.0'
            or not isinstance(version, str) or not 1 <= len(version) <= 80
            or version == 'demo-k1' or review['staging_sha256'] != sha256(staging_bytes).hexdigest()):
        raise ValueError('Invalid release version or staging digest')

    sources = {}
    for row in staging['rows']:
        ref = row['source_ref']
        key = (ref['file_sha256'], ref['sheet'], ref['row'])
        sources[key] = set(ref['columns'])

    entries = [KnowledgeEntry.model_validate(entry) for entry in review['entries']]
    if not entries or len({entry.entry_id for entry in entries}) != len(entries):
        raise ValueError('Release must have nonempty, unique entries')
    approved = [entry for entry in entries if entry.status == 'approved']
    if not approved:
        raise ValueError('Release requires manually approved entries')
    reviewers = review['reviewers']
    if (not isinstance(reviewers, list) or
            len({name.strip() for name in reviewers if isinstance(name, str) and name.strip()}) < 2):
        raise ValueError('Release requires two distinct reviewers')
    for entry in entries:
        if entry.knowledge_version != version or entry.status == 'draft':
            raise ValueError('Release contains a different version or a draft')
        if entry.status == 'approved' and (entry.issue_codes or not (entry.approved_text or '').strip()
                                           or entry.reviewer not in reviewers or not entry.scope):
            raise ValueError('Approved entry is missing scope, text or review, or has open issues')
        if entry.status == 'approved':
            check_template(entry)
            if not set(REQUIRED_SLOTS.get(entry.scenario_id, ())) <= set(entry.required_slots):
                raise ValueError('Reviewed authorization entry omits required slots')
            if entry.scenario_id == 'demo.authorization_chain':
                refs = {(ref.sheet, ref.row, col) for ref in entry.source_refs for col in ref.columns}
                if (SCRIPT_SHEET, 27, 'B') not in refs or (SCRIPT_SHEET, 50, 'B') in refs:
                    raise ValueError('Generic authorization chain requires the reviewed general source')
        for ref in entry.source_refs:
            key = (ref.file_sha256, ref.sheet, ref.row)
            if entry.status == 'approved' and any((ref.sheet, ref.row, col) in QUARANTINE for col in ref.columns):
                raise ValueError('Unresolved quarantined source')
            if key not in sources or not ref.columns or not set(ref.columns) <= sources[key]:
                raise ValueError(f'Unknown source for entry {entry.entry_id}')
    return version, entries


def publish(version, entries):
    from sqlalchemy import select, text
    from sqlalchemy.dialects.postgresql import insert
    from app.db import Knowledge, Session

    proposed = {str(entry.entry_id): entry.model_dump(mode='json') for entry in entries}
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': 'knowledge:' + version})
        active = {row.id: row.data for row in db.scalars(select(Knowledge).where(Knowledge.version == version))}
        if active:
            if active != proposed:
                raise ValueError('Existing release is immutable')
            return
        # A conflicting entry ID rolls back the whole transaction.
        db.execute(insert(Knowledge), [{'id': entry_id, 'version': version, 'data': data}
                                       for entry_id, data in proposed.items()])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('staging', type=Path)
    parser.add_argument('review', type=Path)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    version, entries = validate(args.staging.read_bytes(), json.loads(args.review.read_bytes()))
    if args.publish:
        publish(version, entries)
    print(f'{version}: {len(entries)} entries {"published" if args.publish else "validated"}')


if __name__ == '__main__':
    main()
