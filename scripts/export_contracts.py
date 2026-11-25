"""Generate OpenAPI and JSON Schema without a database or cloud connection."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.contracts import PUBLIC_MODELS
from app.staff_contracts import PUBLIC_STAFF_MODELS
PUBLIC_MODELS = [*PUBLIC_MODELS,*PUBLIC_STAFF_MODELS]
from app.realtime.wire import IceServer, RealtimeOffer, RealtimeAnswer, RealtimeIce, RealtimeControl
PUBLIC_MODELS = [*PUBLIC_MODELS, IceServer, RealtimeOffer, RealtimeAnswer, RealtimeIce, RealtimeControl]
from app.main import app
from pydantic.json_schema import models_json_schema

def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

target=ROOT/'docs/design/contracts'
_,schema=models_json_schema([(m,'validation') for m in PUBLIC_MODELS],title='JD POP contracts 0.4.1')
schema['$schema']='https://json-schema.org/draft/2020-12/schema'
dump(target/'schema.json',schema)
spec=app.openapi()
# Include SSE and internal contract objects even if no REST response directly references them.
_,definitions=models_json_schema([(m,'validation') for m in PUBLIC_MODELS],ref_template='#/components/schemas/{model}')
for name,value in definitions['$defs'].items():
    spec.setdefault('components',{}).setdefault('schemas',{}).setdefault(name,value)
dump(target/'openapi.json',spec)
print('Exported OpenAPI and JSON Schema 0.4.1')
