"""One real request per invocation after credit confirmation. Never enables the running app."""
import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.config import settings
from app.adapters import DomainError
from app.cloud_budget import reserve_call


async def main(provider):
    if not settings.free_quota_confirmed:
        raise DomainError('QUOTA_NOT_CONFIRMED','请先确认赠送额度',503)
    # Only this explicit probe uses cloud access. Global application switches remain unchanged.
    config=replace(settings,cloud_calls_enabled=True)
    if provider=='deepseek':
        from app.deepseek import DeepSeekModel
        from app.contracts import ModelRequest,ModelMessage
        body=ModelRequest(messages=[ModelMessage(role='user',content='请只回答：连接成功')],fixture_response='连接成功',allow_rephrase=True)
        chunks=[c async for c in DeepSeekModel(config,reserve=reserve_call).stream(body,asyncio.Event())]
        print('DeepSeek probe passed. Text characters:',len(''.join(chunks)))
    else:
        from app.tts import QwenTTS,SpeechRequest
        chunks=[c async for c in QwenTTS(config,reserve=reserve_call).stream(SpeechRequest(input='你好，这是合成语音连接测试。'),asyncio.Event())]
        target=ROOT/'.cache/qwen-tts-probe.wav'
        target.parent.mkdir(exist_ok=True)
        with wave.open(str(target),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000)
            output.writeframes(b''.join(chunks))
        print('Qwen probe passed. Local synthetic WAV:',target)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('provider',choices=['deepseek','tts'])
    args=parser.parse_args()
    try:asyncio.run(main(args.provider))
    except DomainError as error:print(error.code+': '+error.message);raise SystemExit(1)
    except Exception as error:
        print('Probe failed:',type(error).__name__,'Check database/configuration locally; no automatic retry.')
        raise SystemExit(1)
