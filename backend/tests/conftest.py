"""Never spend real provider credit in automated tests.
Cloud adapter tests inject explicit config + httpx.MockTransport instead.
"""
import os
os.environ['MODEL_PROVIDER']='fake'
os.environ['VOICE_ENABLED']='false'
os.environ['CLOUD_CALLS_ENABLED']='false'
os.environ['FREE_QUOTA_CONFIRMED']='false'
os.environ['VOICE_WRITES_ENABLED']='false'
os.environ['TTS_PROVIDER']='manual'
