param([switch]$Restart,[switch]$Build)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$taskPython=Join-Path $taskRoot '../../.venv/Scripts/python.exe'
if(!(Test-Path -LiteralPath $taskPython)){$taskPython='python'}
$taskCache=Join-Path $taskRoot '.cache/ddl'
New-Item -ItemType Directory -Path $taskCache -Force | Out-Null
$taskState=Join-Path $taskCache 'services.json'
$taskPrevious=if(Test-Path -LiteralPath $taskState){Get-Content -LiteralPath $taskState -Raw|ConvertFrom-Json}else{$null}
if(!$taskPrevious -and (Test-Path -LiteralPath '.cache/workbench/demo-services.json')){$taskPrevious=Get-Content '.cache/workbench/demo-services.json' -Raw|ConvertFrom-Json}
foreach($taskPort in @(13000,18000)){
  $taskListener=Get-NetTCPConnection -State Listen -LocalPort $taskPort -ErrorAction SilentlyContinue
  if(!$taskListener){continue}
  if(!$Restart){throw "Port $taskPort already active. Use -Restart to restart only this recorded workbench."}
  foreach($taskSocket in $taskListener){
    $taskProcess=Get-CimInstance Win32_Process -Filter "ProcessId = $($taskSocket.OwningProcess)"
    $taskOwned=$taskPrevious -and (($taskPort -eq 13000 -and $taskProcess.ProcessId -eq $taskPrevious.frontend_pid -and $taskProcess.CommandLine -match 'next.+13000') -or ($taskPort -eq 18000 -and ($taskProcess.ProcessId -eq $taskPrevious.backend_pid -or $taskProcess.ParentProcessId -eq $taskPrevious.backend_pid) -and $taskProcess.CommandLine -match 'uvicorn.+18000'))
    if(!$taskOwned){throw "Port $taskPort is not owned by the recorded workbench; no process was stopped."}
    Stop-Process -Id $taskProcess.ProcessId
  }
}
if(!$env:WORKBENCH_DATABASE_URL){$env:WORKBENCH_DATABASE_URL='postgresql+psycopg://v04latency@127.0.0.1:55439/zhiyu_workbench_business_demo_20261125'}
$env:DATABASE_URL=$env:WORKBENCH_DATABASE_URL
$env:APP_ENV='development';$env:PYTHONPATH='backend';$env:PYTHONIOENCODING='utf-8'
$env:PUBLIC_ORIGIN='http://localhost:13000';$env:BACKEND_URL='http://127.0.0.1:18000'
if(!$env:STAFF_PASSWORD){$env:STAFF_PASSWORD='staff-local-only'}
$env:DEMO_PASSWORD='demo-local-only'
$env:SESSION_COOKIE_NAME='zhiyu_ddl_session';$env:STAFF_COOKIE_NAME='zhiyu_ddl_staff'
$taskSecret=Join-Path $taskCache 'cookie-secret'
if(!(Test-Path -LiteralPath $taskSecret)){[guid]::NewGuid().ToString('N')+[guid]::NewGuid().ToString('N')|Set-Content -LiteralPath $taskSecret -Encoding utf8}
$env:COOKIE_SECRET=(Get-Content -LiteralPath $taskSecret -Raw).Trim()
if(!$env:VOICE_CONFIG_ENV_FILE){$env:VOICE_CONFIG_ENV_FILE=(Join-Path $taskRoot '../../.env')}
$env:MODEL_PROVIDER='fake';$env:VOICE_ENABLED='false';$env:VOICE_WRITES_ENABLED='false'
$env:CLOUD_CALLS_ENABLED='true';$env:FREE_QUOTA_CONFIRMED='true';$env:TTS_PROVIDER='qwen';$env:REALTIME_WEBRTC_ENABLED='true'
$env:REALTIME_APPLICATION_IDS='00000000-0000-0000-0000-000000000001,00000000-0000-0000-0000-000000000002,00000000-0000-0000-0000-000000000003'
$env:REALTIME_STUN_URLS='[]';$env:REALTIME_TURN_URLS='';$env:MAX_DAILY_NATIVE_SESSIONS='40';$env:MAX_DAILY_NATIVE_TURNS='8';$env:MAX_DAILY_TTS_CALLS='400'
$env:ZHIYU_KNOWLEDGE_VERSION='workbench-reviewed-20261125-v3'
if($Build -or !(Test-Path -LiteralPath 'frontend/.next/BUILD_ID')){npm run build --prefix frontend -- --webpack;if($LASTEXITCODE -ne 0){throw 'Frontend build failed'}}
$taskBackend=Start-Process -FilePath $taskPython -ArgumentList @('-m','uvicorn','workbench_runtime:app','--app-dir','scripts','--host','127.0.0.1','--port','18000','--no-access-log') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskCache 'backend.out.log') -RedirectStandardError (Join-Path $taskCache 'backend.err.log')
$taskFrontend=Start-Process -FilePath (Get-Command node).Source -ArgumentList @('node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','13000') -WorkingDirectory (Join-Path $taskRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskCache 'frontend.out.log') -RedirectStandardError (Join-Path $taskCache 'frontend.err.log')
@{backend_pid=$taskBackend.Id;frontend_pid=$taskFrontend.Id;frontend='http://localhost:13000';backend='http://127.0.0.1:18000';knowledge_version=$env:ZHIYU_KNOWLEDGE_VERSION}|ConvertTo-Json|Set-Content -LiteralPath $taskState -Encoding utf8
$taskReadiness=@'
import time,urllib.request
client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for target in ['http://127.0.0.1:18000/health/ready','http://localhost:13000']:
    for attempt in range(80):
        try:
            with client.open(target,timeout=2) as response:
                if response.status==200:break
        except OSError:time.sleep(.25)
    else:raise SystemExit('Service not ready; inspect .cache/ddl logs')
'@
$taskReadiness | & $taskPython -
if($LASTEXITCODE -ne 0){throw 'Readiness check failed; existing database was not changed'}
Write-Output 'Ready: http://localhost:13000 | staff-local-only (unless STAFF_PASSWORD overridden). Cloud calls only on Start call; voice writes disabled.'
