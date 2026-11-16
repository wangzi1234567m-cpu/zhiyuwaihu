param([ValidateSet('Backend','Frontend','Migrate','Check','Types')][string]$Action='Check')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$taskPython=Join-Path $taskRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython='python' }
$env:PYTHONPATH=Join-Path $taskRoot 'backend'
switch ($Action) {
  'Backend' { & $taskPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log }
  'Frontend' { npm run dev --prefix frontend }
  'Migrate' {
    & $taskPython -m alembic -c backend/alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $taskPython -m app.seed
  }
  'Types' {
    & $taskPython scripts/export_contracts.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm run types --prefix frontend
  }
  'Check' {
    & $taskPython -m pytest backend/tests -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm run typecheck --prefix frontend
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm run build --prefix frontend
  }
}
exit $LASTEXITCODE
