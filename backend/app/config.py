import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv('DATABASE_URL', 'postgresql+psycopg://zhiyu:local-dev-only@localhost:5432/zhiyu')
    environment: str = os.getenv('APP_ENV', 'development')
    cookie_secret: str = os.getenv('COOKIE_SECRET', 'local-development-secret-change-before-exposure')
    public_origin: str = os.getenv('PUBLIC_ORIGIN', 'http://localhost:3000')
    demo_password: str = os.getenv('DEMO_PASSWORD', 'demo-local-only')
    token_delay: float = float(os.getenv('FAKE_TOKEN_DELAY', '0.015'))
    faults_enabled: bool = os.getenv('ENABLE_FAULTS', 'false').lower() == 'true'

settings = Settings()
