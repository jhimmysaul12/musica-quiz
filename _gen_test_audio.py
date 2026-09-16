"""Genera un audio de prueba sin copyright: arpegio de 5 notas (tono limpio)."""
import wave, struct, math

sr = 44100
def nota(freq, dur):
    n = int(sr * dur)
    # seno + armónico suave — sin sample de terceros
    return [0.45 * math.sin(2 * math.pi * freq * t / sr)
            + 0.15 * math.sin(2 * math.pi * (freq * 2) * t / sr) * (1 - t / n)
            for t in range(n)]

def silencio(dur):
    return [0.0] * int(sr * dur)

notas = [nota(523, 0.35), nota(659, 0.35), nota(784, 0.35), nota(1047, 0.55)]
data = sum(notas, [])
# normalizar
m = max(1e-9, max(abs(x) for x in data))
data = [x * 0.95 / m for x in data]
with wave.open('static/quiz-audio/test_melody.wav', 'w') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(b''.join(struct.pack('<h', int(x * 32767)) for x in data))
print('generado static/quiz-audio/test_melody.wav')
