"""Prueba del sistema de música ambiental: upload, estado, descarga, delete."""
import requests
import io
import wave
import math

BASE = 'http://localhost:5000'

# generar WAV mínimo de prueba
frames = b''.join(
    int(12000 * (0.5 + 0.5 * math.sin(i * 0.05))).to_bytes(2, 'little', signed=True)
    for i in range(8000))
buf = io.BytesIO()
w = wave.open(buf, 'wb')
w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
w.writeframes(frames); w.close()
buf.seek(0)

s = requests.Session()
r = s.post(BASE + '/admin/login', data={'password': 'admin123'})
print('login:', r.status_code)

r = s.post(BASE + '/api/ambient/victoria', files={'audio': ('test.wav', buf, 'audio/wav')})
print('upload victoria:', r.status_code, r.json())
assert r.status_code == 200

d = requests.get(BASE + '/api/ambient').json()
print('ambient:', d)
assert d['victoria'], 'no quedó guardado'

r2 = requests.get(BASE + d['victoria'])
print('descarga:', r2.status_code, len(r2.content), 'bytes')
assert r2.status_code == 200 and len(r2.content) > 1000

# slot inválido
r3 = s.post(BASE + '/api/ambient/malo', files={'audio': ('test.wav', buf, 'audio/wav')})
print('slot inválido:', r3.status_code)
assert r3.status_code == 400

# delete
r4 = s.delete(BASE + '/api/ambient/victoria')
print('delete:', r4.status_code)
d = requests.get(BASE + '/api/ambient').json()
assert not d['victoria']
print('AMBIENT: TODO OK ✅')
