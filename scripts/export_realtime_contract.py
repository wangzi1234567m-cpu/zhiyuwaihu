"""Export internal V0.4 schema without importing the app, database or providers."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from pydantic.json_schema import models_json_schema
from app.realtime_contract.dto import PUBLIC_MODELS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    _, schema = models_json_schema([(m, 'validation') for m in PUBLIC_MODELS],
                                  title='Realtime business internal contract 0.4.0')
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    body = json.dumps(schema, ensure_ascii=False, indent=2) + '\n'
    target = ROOT / 'docs/design/contracts/realtime-v04.schema.json'
    if args.check:
        if not target.exists() or target.read_text(encoding='utf-8') != body:
            raise SystemExit('V0.4 schema is stale; run scripts/export_realtime_contract.py')
        print('V0.4 schema matches Pydantic source')
    else:
        target.write_text(body, encoding='utf-8')
        print('Exported V0.4 internal schema')


if __name__ == '__main__':
    main()
