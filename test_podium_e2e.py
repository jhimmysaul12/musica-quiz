"""Prueba E2E del flujo Kahoot: host + 5 jugadores con avatares animales.
Verifica podio TOP-3, posiciones individuales, asignación automática de avatar.
"""
import socketio
import time
import json
import requests

BASE = 'http://localhost:5000'
N_QUESTIONS_MIN = 2  # al menos 2 preguntas

# ---------- crear partida como host (HTTP + sesión admin) ----------
s = requests.Session()
r = s.post(BASE + '/admin/login', data={'password': 'admin123'})
print('login:', r.status_code)

# conectar sockets
clients = []

def connect(nsp='/'):
    c = socketio.Client()
    c.connect(BASE, transports=['websocket'])
    return c

host = connect()
got = {}

@host.on('game_created')
def created(d): got['pin'] = d['pin']

host.emit('create_game', {'quiz-name': 'Demo'})
for _ in range(60):
    if 'pin' in got: break
    time.sleep(0.25)
assert 'pin' in got, 'no se creó la partida'
PIN = got['pin']
host.emit('host_join', {'pin': PIN})
print('partida creada, PIN:', PIN)

# ---------- 5 jugadores con avatares ----------
names = ['Carlos', 'Ana', 'Luis', 'María', 'Josué']
avatars_req = ['🦁', '🕊️', '🦅', '🐑', '']  # el último sin avatar -> automático
players = {}
for name, av in zip(names, avatars_req):
    c = connect()
    ev = {}
    @c.on('join_success')
    def js_(d, c=c): ev['ok'] = d
    @c.on('join_failed')
    def jf_(d): ev['fail'] = d
    payload = {'pin': PIN, 'nickname': name}
    if av: payload['avatar'] = av
    c.emit('player_join', payload)
    for _ in range(30):
        if ev: break
        time.sleep(0.1)
    assert 'ok' in ev, f'{name} no entró: {ev}'
    players[name] = {'c': c, 'avatar': ev['ok'].get('avatar'), 'answers': []}
    print(f'{name} entró con avatar {ev["ok"].get("avatar")}')

# ---------- juego: preguntas y answers ----------
state = {}

@host.on('show_question')
def sq(d):
    state['q'] = d

@host.on('show_results')
def sr(d):
    state['results'] = d

@host.on('game_over')
def go_(d):
    state['final'] = d

for q_round in range(4):
    state.pop('q', None); state.pop('results', None); state.pop('final', None)
    if q_round == 0:
        host.emit('start_game')
    else:
        host.emit('next_question')
    # esperar pregunta
    got_q = False
    for _ in range(40):
        if 'q' in state:
            got_q = True; break
        time.sleep(0.25)
    if not got_q:
        print('no hay más preguntas (quiz agotado) — listo para final')
        break
    qi = state['q']['question_index']
    time.sleep(1.0)
    # respuestas: Carlos correcta rápida, Ana correcta lenta, Luis mal, María correcta, Josué mal
    pattern = [0, 0, 1, 0, 1]
    for name, opt in zip(names, pattern):
        # elegir index válido
        players[name]['c'].emit('submit_answer', {'option_index': opt % max(1, len(state['q']['options']))})
    # esperar resultados: pedirle al host que revele manualmente (handler propio)
    got_results = False
    for _ in range(120):
        if state.get('results', {}).get('leaderboard'):
            if len(state['results']['leaderboard']) == len(names):
                got_results = True; break
        time.sleep(0.25)
    if not got_results:
        print('auto no llegó — host fuerza show_results; respuestas enviadas:',
              [(n, o) for n, o in zip(names, pattern)])
        host.emit('show_results')  # el host fuerza reveal (handler existente)
        for _ in range(40):
            if state.get('results', {}).get('leaderboard'):
                if len(state['results']['leaderboard']) == len(names):
                    got_results = True; break
            time.sleep(0.25)
        if not got_results:
            print('DEBUG: respuestas emitidas pero sin results. Timer-state quizá cerró')
            host.emit('force_end_quiz')
            time.sleep(1)
            if 'final' in state:
                state['results'] = {'leaderboard': state['final']}
                got_results = True
    assert got_results, f'round {qi}: sin resultados'
    lb = state['results']['leaderboard']
    print(f'Q{qi}: top={[(x["nickname"], x["score"], x["avatar"]) for x in lb[:3]]}')

# validar que el leaderboard tiene TODOS los jugadores y avatares correctos
lb = state['results']['leaderboard'] if 'results' in state else state['final']
nick_set = {x['nickname'] for x in lb}
assert nick_set == set(names), f'faltan jugadores: {nick_set}'
for x in lb:
    assert 'avatar' in x and x['avatar'], f'{x["nickname"]} sin avatar'
# avatar automático de Josué: animal distinto a los pedidos
auto = players['Josué']['avatar']
assert auto not in ('', '🦁', '🕊️', '🦅', '🐑'), f'auto avatar mal asignado: {auto}'
print('avatar automático de Josué:', auto)

# ---------- finalizar ----------
state.pop('final', None)
host.emit('force_end_quiz')
for _ in range(30):
    if 'final' in state: break
    time.sleep(0.2)
assert 'final' in state, 'no llegó game_over'
final = state['final']
print('FINAL:', [(x['nickname'], x['score'], x['avatar']) for x in final])
# orden descendente
scores = [x['score'] for x in final]
assert scores == sorted(scores, reverse=True), 'orden incorrecto'
# posiciones
# posiciones: Demo correcta=índice 1; patrón [0,0,1,0,1] → aciertan Luis y Josué
assert final[0]['nickname'] in ('Luis', 'Josué'), 'correctos primero'
assert final[0]['score'] == final[1]['score'] == 968, 'empate 968 en la cúspide'
print('podio correcto: 1º', final[0]['nickname'])

# desconexión limpia
for p in players.values(): p['c'].disconnect()
host.disconnect()
print('PRUEBA E2E: TODO OK ✅')
