"""Prueba end-to-end de Música Quiz: host crea partida, jugadores entran,
responden, puntúan, timer, ranking y game over."""
import socketio
import time

BASE = 'http://localhost:5000'
events = []  # lista de (nombre, data) para reportar


def mk_client(role):
    s = socketio.Client()
    s.role = role
    @s.on('connect')
    def c(): events.append((role, 'connected'))
    return s


HOST = mk_client('host')
P1 = mk_client('p1:Ana')
P2 = mk_client('p2:Beto')
P3 = mk_client('p3:Cata')

@HOST.on('game_created')
def hc(d): events.append(('host', f'PIN={d["pin"]}')); HOST.pin = d['pin']

@HOST.on('create_failed')
def hf(d): events.append(('host', f'create_failed {d}'))

@P1.on('join_success')
def j1(d): events.append(('p1', 'join_success'))
@P2.on('join_success')
def j2(d): events.append(('p2', 'join_success'))
@P3.on('join_success')
def j3(d): events.append(('p3', 'join_success'))

@P1.on('join_failed')
def jf1(d): events.append(('p1', f'join_failed {d}'))
@P2.on('join_failed')
def jf2(d): events.append(('p2', f'join_failed {d}'))
@P3.on('join_failed')
def jf3(d): events.append(('p3', f'join_failed {d}'))

for s in (P1, P2, P3):
    s.last_q = None
    @s.on('show_question')
    def sq(d):
        events.append((s.role, f"Q{d['question_index']+1}: {d['text'][:40]}... time={d['time']} cat={d['category']}"))
        s.last_q = d

    @s.on('show_results')
    def sr(d):
        events.append((s.role, f"resultado correcto={d['correct_option']} lb={[(p['nickname'], p['score']) for p in d['leaderboard'][:3]]}"))
        s.last_r = d

    @s.on('game_over')
    def go(d):
        events.append((s.role, f"game_over ganador={d[0]['nickname']} pts={d[0]['score']}"))

    @s.on('time_up')
    def tu(d): events.append((s.role, 'time_up'))

    @s.on('answer_rejected')
    def ar(d): events.append((s.role, f'rejected: {d["reason"]}'))

P1.on('answer_received', lambda: events.append(('p1', 'respuesta aceptada')))
P2.on('answer_rejected', lambda d: events.append(('p2', f'rejected: {d["reason"]}')))

# --- conectar
HOST.connect(BASE, wait_timeout=5)
P1.connect(BASE, wait_timeout=5)
P2.connect(BASE, wait_timeout=5)
P3.connect(BASE, wait_timeout=5)

# --- host crea la partida
HOST.emit('create_game', {'quiz-name': 'Musica Cristiana Vol 1'})
time.sleep(0.5)
pin = HOST.pin
print(f"PIN generado: {pin}")

# --- jugador entra con PIN incorrecto
P3.emit('player_join', {'pin': '9999', 'nickname': 'Cata'})
time.sleep(0.3)

# --- jugadores entran bien
P1.emit('player_join', {'pin': pin, 'nickname': 'Ana'}); time.sleep(0.2)
P2.emit('player_join', {'pin': pin, 'nickname': 'Beto'}); time.sleep(0.2)
P3.emit('player_join', {'pin': pin, 'nickname': 'Cata'}); time.sleep(0.3)

# --- host inicia
HOST.emit('host_join', {'pin': pin}); time.sleep(0.2)
HOST.emit('start_game'); time.sleep(0.6)

# --- Ana responde rápido la primera pregunta
q = P1.last_q
print(f"pregunta 1: time={q['time']}")
P1.emit('submit_answer', {'option_index': 2})
time.sleep(0.3)
# intento de doble respuesta (debe rechazar)
P1.emit('submit_answer', {'option_index': 0})
time.sleep(0.3)

# Beto responde correcto
P2.emit('submit_answer', {'option_index': 1})
time.sleep(0.3)

# --- host revela resultados
HOST.emit('show_results')
time.sleep(0.5)

# --- siguiente pregunta
HOST.emit('next_question'); time.sleep(0.5)
# P2 tarda mucho en responder usando racha
P2.emit('submit_answer', {'option_index': 1}); time.sleep(0.3)
# P1 se equivoca
P1.emit('submit_answer', {'option_index': 0}); time.sleep(0.3)
HOST.emit('show_results'); time.sleep(0.5)

# --- 3ra pregunta: nadie responde, debe saltar time_up
HOST.emit('next_question'); time.sleep(q['time'] + 3)

# --- 4ta pregunta: responderla y forzar fin
try:
    P2.emit('submit_answer', {'option_index': 1}); time.sleep(0.3)
    HOST.emit('force_end_quiz'); time.sleep(0.6)
except Exception as e:
    events.append(('test', f'err {e}'))

for s in (HOST, P1, P2, P3):
    s.disconnect()

print("\n===== EVENTOS CAPTURADOS =====")
for r, msg in events:
    print(f"  [{r:8s}] {msg}")
print("\nPRUEBA COMPLETADA")
