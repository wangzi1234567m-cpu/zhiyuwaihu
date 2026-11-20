"""Convert private source files to a review-only staging artifact."""
import argparse
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook

MANIFEST = Path(__file__).resolve().parents[1] / 'docs/design/source-manifest.json'
MAX_BYTES = 32 * 1024 * 1024
MAIN = '场景汇总'


def sources(inputs, allowed):
    files = {}
    for path in inputs:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError(f'Input too large: {path.name}')
        if path.suffix.lower() == '.zip':
            with ZipFile(path) as archive:
                for member in archive.infolist():
                    name = member.filename
                    if not member.flag_bits & 0x800:
                        # Some ZIP writers omit the UTF-8 filename flag.
                        try:
                            name = name.encode('cp437').decode('utf-8')
                        except UnicodeDecodeError:
                            pass
                    parts = name.replace(chr(92), '/').split('/')
                    if member.is_dir() or '__MACOSX' in parts:
                        continue
                    if name.startswith(('/', chr(92))) or '..' in parts or ':' in parts[0]:
                        raise ValueError('Unsafe archive path')
                    if parts[-1] in allowed:
                        if member.file_size > MAX_BYTES or parts[-1] in files:
                            raise ValueError(f'Invalid or duplicate source: {parts[-1]}')
                        files[parts[-1]] = archive.read(member)
        elif path.name in allowed and path.name not in files:
            files[path.name] = path.read_bytes()
        else:
            raise ValueError(f'Unknown or duplicate source: {path.name}')
    return files


def convert(inputs):
    catalog = json.loads(MANIFEST.read_text(encoding='utf-8'))
    expected = {item['filename']: item for item in catalog['files']}
    files = sources(inputs, expected)
    entries, issues, manifest = [], [], []
    counts = Counter()
    for name, definition in expected.items():
        if name not in files:
            if 'sheets' in definition:
                raise ValueError(f'Missing workbook: {name}')
            continue
        digest = sha256(files[name]).hexdigest()
        if digest != definition['sha256']:
            raise ValueError(f'Unreviewed source version: {name}')
        manifest.append({'filename': name, 'sha256': digest, 'sheets': definition.get('sheets', [])})
        if 'sheets' not in definition:
            continue
        workbook = load_workbook(BytesIO(files[name]), data_only=False)
        try:
            for sheet_name in definition['sheets']:
                sheet = workbook[sheet_name]
                is_main = sheet_name == MAIN
                categories = {}
                is_taxonomy = is_main or sheet_name in ('京麦驳回场景', '电话场景')
                if is_taxonomy:
                    for region in sheet.merged_cells.ranges:
                        if region.min_col == region.max_col and region.max_col <= 3:
                            for row in range(max(13, region.min_row), region.max_row + 1):
                                categories[row, region.min_col] = sheet.cell(region.min_row, region.min_col).value
                rows = sheet.iter_rows(min_row=13, max_row=80) if is_main else sheet.iter_rows()
                for cells in rows:
                    row = cells[0].row
                    raw = {c.column_letter: c.value for c in cells if c.value is not None}
                    if not raw and not is_main:
                        continue
                    source_ref = {'file_sha256': digest, 'sheet': sheet_name, 'row': row,
                                  'columns': list(raw)}
                    channel = str(raw.get('D', '')).strip() if is_main else None
                    if is_main:
                        counts[channel] += 1
                    notes = {c.column_letter: c.comment.text for c in cells if c.comment}
                    formulas = [c.column_letter for c in cells if c.data_type == 'f']
                    issue_codes = ['formula_requires_review'] if formulas else []
                    entries.append({'source_ref': source_ref, 'raw_cells': raw,
                                    'classification': [categories.get((row, i), raw.get(col))
                                                       for i, col in enumerate('ABC', 1)] if is_taxonomy else [],
                                    'channel': {'电话': 'phone', '京麦驳回': 'jingmai'}.get(channel),
                                    'internal_comments': notes, 'status': 'draft',
                                    'issue_codes': issue_codes})
                    issues.extend({'source_ref': source_ref, 'code': code} for code in issue_codes)
                if is_main:
                    entries.append({'source_ref': {'file_sha256': digest, 'sheet': sheet_name,
                                                   'row': 2, 'columns': ['A']},
                                    'raw_cells': {'A': sheet['A2'].value}, 'status': 'draft'})
        finally:
            workbook.close()
    if dict(counts) != {'电话': 54, '京麦驳回': 14}:
        raise ValueError(f'Unexpected main sheet channel counts: {dict(counts)}')
    return {'schema_version': '0.3.0', 'source_manifest': manifest, 'issues': issues,
            'counts': {'main_total': sum(counts.values()), 'main_channels': dict(counts)},
            'mappings': [], 'rows': entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True, help='Private path outside the repository')
    args = parser.parse_args()
    output = args.output.resolve()
    root = Path(__file__).resolve().parents[1]
    if output.is_relative_to(root) and not output.is_relative_to(root / 'private-evidence'):
        parser.error('Use private-evidence/ or a directory outside the checkout')
    package = convert(args.inputs)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(package, handle, ensure_ascii=False, indent=2, default=str)


if __name__ == '__main__':
    main()
