"""Loopback demonstration host. Reuse existing provider configuration explicitly.

Starting/reading capabilities never calls a cloud provider. Voice writes stay off.
"""
import os
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
CONFIG=Path(os.environ.get('VOICE_CONFIG_ENV_FILE',str(ROOT.parents[1]/'.env'))).resolve()
PROVIDER_FIELDS=('DASHSCOPE_API_KEY','DASHSCOPE_WORKSPACE_ID','DASHSCOPE_REGION',
    'DASHSCOPE_TTS_MODEL','DASHSCOPE_TTS_VOICE','DASHSCOPE_TTS_HTTP_URL')
values=dotenv_values(CONFIG) if CONFIG.is_file() else {}
for name in PROVIDER_FIELDS:
    if values.get(name) and not os.environ.get(name):os.environ[name]=values[name]

from app.main import create_app
from app.config import settings
from app.cloud_budget import reserve_call
from app.realtime.provider import NativeInput
from app.realtime.speech import StreamingSpeech

# Same bounded profile as the previously verified manual host, not new limits.
# NativeInput reserves eight turns up front; 320 is the existing daily total cap.
LIMITS={'native_realtime_session':40,'native_realtime_turn':320,'tts':400}
def reserve(kind,unused_per_connection_limit):
    reserve_call(kind,LIMITS[kind])  # ordinary persistent ledger; no reset/new namespace

if settings.voice_writes_enabled:raise RuntimeError('This demonstration requires voice writes disabled')
app=create_app(realtime_input_factory=lambda:NativeInput(settings,reserve),
               realtime_speech_factory=lambda:StreamingSpeech(settings,reserve))
