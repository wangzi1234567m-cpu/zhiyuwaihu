import asyncio
from dataclasses import replace
from types import SimpleNamespace
import httpx
import pytest
from app.config import settings
from app.realtime.diagnostics import RealtimeProbe
from app.realtime.provider import NativeInput, ProviderFailure
from test_realtime_provider import SocketBoundary
from test_tts import configured, frame
from app.tts import QwenTTS, SpeechRequest


@pytest.mark.parametrize('silence',[400,300,250])
async def test_vad_configuration_reaches_provider_without_changing_other_parameters(silence):
    c=replace(configured(),realtime_vad_silence_ms=silence,max_daily_native_turns=1)
    socket=SocketBoundary([{'type':'session.created'},{'type':'session.updated'}])
    async def connect(*args,**kwargs):return socket
    provider=NativeInput(c,lambda *args:None,connect)
    try:
        await provider.open(SimpleNamespace(state='VERIFYING'))
        session=socket.sent[0]['session']
        assert session['turn_detection']=={'type':'server_vad','threshold':0.5,'silence_duration_ms':silence}
        assert session['modalities']==['text'] and session['audio']['input']['format']['sample_rate']==16000
    finally:await provider.close()


@pytest.mark.parametrize('silence,threshold',[(0,.5),(200,.5),(401,.5),(300,0),(300,1),(300,float('nan'))])
async def test_invalid_vad_fails_before_connection_or_budget(silence,threshold):
    calls=[]
    provider=NativeInput(replace(configured(),realtime_vad_silence_ms=silence,realtime_vad_threshold=threshold),lambda *a:calls.append(a))
    with pytest.raises(ProviderFailure,match='NATIVE_VAD_CONFIG_INVALID'):
        await provider.open(SimpleNamespace(state='VERIFYING'))
    assert not calls and provider.ws is None


def test_probe_is_opt_in_bounded_and_high_resolution():
    disabled=RealtimeProbe();disabled.emit('not_retained')
    assert not disabled.events
    probe=RealtimeProbe(True,capacity=2)
    for i in range(3):probe.emit('frame',sequence=i)
    result=probe.snapshot()
    assert result['dropped']==1 and len(result['events'])==2
    assert result['events'][1]['at_ns']>=result['events'][0]['at_ns']
    assert result['clock_domain']=='server.perf_counter'


async def test_tts_diagnostics_record_headers_before_pcm_without_secrets_or_text():
    requests=[]
    async def upstream(request):
        requests.append(request)
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=frame(b'\0\1'*32,True))
    tts=QwenTTS(configured(realtime_diagnostics_enabled=True),httpx.MockTransport(upstream))
    chunks=[chunk async for chunk in tts.stream(SpeechRequest(input='私有正文不应进入诊断'),asyncio.Event())]
    names=[event['event'] for event in tts.probe.events]
    assert names.index('tts_request_start')<names.index('tts_response_headers')<names.index('tts_first_audio_block')<=names.index('tts_first_pcm')<names.index('tts_stream_end')
    assert len(requests)==1 and b''.join(chunks)==b'\0\1'*32
    dump=str(tts.probe.snapshot())
    assert 'test-private-bailian-key' not in dump and '私有正文' not in dump


def test_extra_identity_setup_successes_cannot_dilute_failure_gate():
    from report_realtime_latency import compare
    metadata={'case_set_sha256':'fixed','network_profile':'same','client':'same','measurement_method':'controlled_input_end_to_remote_pcm'}
    success=lambda cohort=True:{'status':'success','effective_answer_ms':1500,'cohort':cohort}
    baseline={'metadata':metadata,'samples':[success() for _ in range(30)]+[{'status':'failed'}]}
    candidate={'metadata':metadata,'samples':[success() for _ in range(30)]+[success(False) for _ in range(30)]+[{'status':'failed'},{'status':'failed'}]}
    result=compare(baseline,candidate)
    assert result['candidate']['valid']==30 and result['candidate']['extra_setup_successes']==30
    assert result['candidate']['failure_rate']<=result['baseline']['failure_rate']
    assert not result['failure_rate_not_worse'] and not result['latency_gate_passed']


def test_opening_failures_are_counted_even_without_turn_samples():
    from report_realtime_latency import summarize
    report={'samples':[{'status':'success','effective_answer_ms':1500} for _ in range(30)],'calls':[{'failure':'OPENING_FAILED','samples':[]},{'failure':'OPENING_FAILED','samples':[]}]}
    result=summarize(report)
    assert result['valid']==30 and result['total']==32 and result['failed_or_missing']==2
    assert result['failure_rate']==2/32 and result['acceptance_failure_rate']==2/32
