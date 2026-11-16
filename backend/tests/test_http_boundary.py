import httpx
from app.main import create_app

async def test_auth_cookie_and_csrf_without_database():
    app=create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/health/live')).status_code==200
        assert (await client.get('/api/v1/capabilities')).status_code==401
        assert (await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})).status_code==403
        result=await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'},headers={'X-Requested-With':'zhiyu'})
        assert result.status_code==200
        assert 'HttpOnly' in result.headers['set-cookie']
        assert (await client.get('/api/v1/capabilities')).json()['voice'] is False

async def test_cloud_voice_endpoint_is_explicitly_disabled():
    app=create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        result=await client.post('/integrations/agora/sessions/00000000-0000-0000-0000-000000000001/v1/chat/completions',json={'messages':[]})
        assert result.status_code==503
        assert result.json()['code']=='VOICE_NOT_CONFIGURED'
