"""One small real model request, only after local quota confirmation switches are set."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.deepseek import DeepSeekModel
from app.contracts import ModelRequest,ModelMessage
from app.cloud_budget import reserve_call
from app.adapters import DomainError

async def main():
    chunks=[]
    async for chunk in DeepSeekModel(reserve=reserve_call).stream(ModelRequest(messages=[
        ModelMessage(role='system',content='请只回答“模型连接成功”，不添加其他文字。'),
        ModelMessage(role='user',content='测试连接')],fixture_response='模型连接成功',allow_rephrase=True),asyncio.Event()):
        chunks.append(chunk)
    print('DeepSeek content received; characters:',len(''.join(chunks)))

if __name__=='__main__':
    try:asyncio.run(main())
    except DomainError as e:print(e.code+': '+e.message);raise SystemExit(1)
