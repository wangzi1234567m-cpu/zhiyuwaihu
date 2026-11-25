from dataclasses import replace
import httpx
from test_integration import setup,pytestmark


async def test_dedicated_workbench_cookies_preserve_legacy_login(setup,monkeypatch):
    import app.main as main
    client,app,services=setup
    monkeypatch.setattr(main,'settings',replace(main.settings,session_cookie_name='zhiyu_ddl_session',staff_cookie_name='zhiyu_ddl_staff'))
    client.cookies.delete('zhiyu_session')
    client.cookies.delete('zhiyu_staff')
    client.cookies.set('zhiyu_session','legacy-session-sentinel')
    client.cookies.set('zhiyu_staff','legacy-staff-sentinel')
    response=await client.post('/api/v1/auth/staff/login',json={'username':'demo','password':'staff-local-only'})
    assert response.status_code==200
    assert client.cookies.get('zhiyu_session')=='legacy-session-sentinel'
    assert client.cookies.get('zhiyu_staff')=='legacy-staff-sentinel'
    assert (await client.get('/api/v1/staff/applications')).status_code==200
    client.cookies.delete('zhiyu_ddl_staff')
    assert (await client.get('/api/v1/staff/applications')).status_code==403
