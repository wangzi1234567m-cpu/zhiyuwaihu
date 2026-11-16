import time
import urllib.request
for url in ['http://127.0.0.1:8000/health/ready','http://localhost:3000']:
    for attempt in range(60):
        try:
            urllib.request.urlopen(url,timeout=2)
            break
        except Exception:
            time.sleep(1)
    else:
        raise SystemExit('Service did not become ready: '+url)
