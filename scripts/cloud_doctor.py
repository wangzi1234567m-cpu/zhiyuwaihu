"""Read configuration presence only. Never prints secrets or calls paid APIs."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.config import settings
from app.voice import AgoraVoiceGateway
from app.tts import QwenTTS
from app.adapters import DomainError

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    for name in ['deepseek_api_key','dashscope_api_key','agora_app_id','agora_certificate','agora_customer_id','agora_customer_secret','integration_secret']:
        print(f'{name}: '+('已填写' if getattr(settings,name) else '待填写'))
    print('模型配置:',settings.model_provider,settings.deepseek_model)
    print('云调用开关:',settings.cloud_calls_enabled)
    print('赠送额度已确认:',settings.free_quota_confirmed)
    print('TTS供应商:',settings.tts_provider)
    if settings.tts_provider=='qwen':
        print('百炼配置:',settings.dashscope_region,settings.dashscope_tts_model,settings.dashscope_tts_voice)
        try:QwenTTS().require();print('百炼本地配置检查通过，仍需真实探针')
        except DomainError as error:print('百炼准备:',error.message)
    print('语音准备:',AgoraVoiceGateway().unavailable_reason() or '配置检查通过，仍需真实云验收')
    print('本次没有调用任何云端接口。')

if __name__=='__main__':main()
