import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Explicit project file; environment supplied by containers always takes precedence.
load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)

@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv('DATABASE_URL', 'postgresql+psycopg://zhiyu:local-dev-only@localhost:5432/zhiyu')
    environment: str = os.getenv('APP_ENV', 'development')
    cookie_secret: str = os.getenv('COOKIE_SECRET', 'local-development-secret-change-before-exposure')
    public_origin: str = os.getenv('PUBLIC_ORIGIN', 'http://localhost:3000')
    demo_password: str = os.getenv('DEMO_PASSWORD', 'demo-local-only')
    token_delay: float = float(os.getenv('FAKE_TOKEN_DELAY', '0.015'))
    faults_enabled: bool = os.getenv('ENABLE_FAULTS', 'false').lower() == 'true'
    model_provider: str = os.getenv('MODEL_PROVIDER', 'fake')
    deepseek_api_key: str = os.getenv('DEEPSEEK_API_KEY', '')
    deepseek_model: str = os.getenv('DEEPSEEK_MODEL', 'deepseek-flash')
    cloud_calls_enabled: bool = os.getenv('CLOUD_CALLS_ENABLED', 'false').lower() == 'true'
    free_quota_confirmed: bool = os.getenv('FREE_QUOTA_CONFIRMED', 'false').lower() == 'true'
    voice_enabled: bool = os.getenv('VOICE_ENABLED', 'false').lower() == 'true'
    voice_writes_enabled: bool = os.getenv('VOICE_WRITES_ENABLED', 'false').lower() == 'true'
    integration_secret: str = os.getenv('INTEGRATION_SECRET', '')
    integration_public_url: str = os.getenv('INTEGRATION_PUBLIC_URL', '')
    agora_app_id: str = os.getenv('AGORA_APP_ID', '')
    agora_certificate: str = os.getenv('AGORA_APP_CERTIFICATE', '')
    agora_customer_id: str = os.getenv('AGORA_CUSTOMER_ID', '')
    agora_customer_secret: str = os.getenv('AGORA_CUSTOMER_SECRET', '')
    agora_region: str = os.getenv('AGORA_REGION', 'cn')
    agora_asr_config: str = os.getenv('AGORA_ASR_CONFIG', '{"language":"zh-CN"}')
    agora_tts_config: str = os.getenv('AGORA_TTS_CONFIG', '{}')
    tts_provider: str = os.getenv('TTS_PROVIDER', 'manual')
    dashscope_api_key: str = os.getenv('DASHSCOPE_API_KEY', '')
    dashscope_region: str = os.getenv('DASHSCOPE_REGION', 'cn-beijing')
    dashscope_workspace_id: str = os.getenv('DASHSCOPE_WORKSPACE_ID', '')
    dashscope_tts_model: str = os.getenv('DASHSCOPE_TTS_MODEL', 'qwen-audio-3.1-tts-flash')
    dashscope_tts_voice: str = os.getenv('DASHSCOPE_TTS_VOICE', 'longanhuan_v3.1')
    dashscope_tts_http_url: str = os.getenv('DASHSCOPE_TTS_HTTP_URL', '')
    tts_first_audio_timeout: float = float(os.getenv('TTS_FIRST_AUDIO_TIMEOUT', '5'))
    tts_total_timeout: float = float(os.getenv('TTS_TOTAL_TIMEOUT', '30'))
    max_daily_tts_calls: int = int(os.getenv('MAX_DAILY_TTS_CALLS', '100'))
    model_first_text_timeout: float = float(os.getenv('MODEL_FIRST_TEXT_TIMEOUT', '2.5'))
    model_total_timeout: float = float(os.getenv('MODEL_TOTAL_TIMEOUT', '8'))
    max_daily_model_calls: int = int(os.getenv('MAX_DAILY_MODEL_CALLS', '30'))
    max_daily_voice_calls: int = int(os.getenv('MAX_DAILY_VOICE_CALLS', '3'))
    staging_password: str = os.getenv('STAGING_PASSWORD', '')

settings = Settings()
