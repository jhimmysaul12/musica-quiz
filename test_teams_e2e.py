"""E2E de modo equipos: host configura 3 equipos, 5 jugadores, reparto, juego, podio de equipos."""
import socketio
import time
import requests

BASE = 'http://localhost:5000'

s = requests.Session()
s.post(BASE + '/admin/login', data={'password': 'admin123'})

def connect():
    c = socketio.Client()
    c.connect(BASE, transports=['websocket'])
    return c

host = connect()
got = {}

@host.on('game_created')
def created(d): got['pin'] = d['pin']
@host.on('update_teams')
def ut(d): got['teams'] = d
@host.on('host_ready')
def hr(d): got['ready'] = d
@host.on('show_results')
def sr_(d): got['results'] = d
@host.on('game_over')
def go_(d): got['final'] = d

host.emit('create_game', {'quiz-name': 'Demo'})
for _ in range(60):
    if 'pin' in got: break
    time.sleep(0.25)
PIN = got['pin']
host.emit('host_join', {'pin': PIN})
time.sleep(0.5)

# host configura 3 equipos
host.emit('setup_teams', {'n': 3})
for _ in range(40):
    if 'teams' in got and len(got.get('teams') or []) == 3: break
    time.sleep(0.2)
assert got.get('teams'), 'no llegó update_teams'
assert len(got['teams']) == 3, f'esperaba 3 equipos: {got["teams"]}'
print('equipos creados:', [t['nombre'] for t in got['teams']])

# 5 jugadores: el reparto debe ser round-robin 0,1,2,0,1
names = ['Carlos', 'Ana', 'Luis', 'María', 'Josué']
expected_team = [0, 1, 2, 0, 1]
players = {}
for i, name in enumerate(names):
    c = connect()
    ev = {}
    @c.on('join_success')
    def js_(d, c=c): ev['ok'] = d
    c.emit('player_join', {'pin': PIN, 'nickname': name})
    for _ in range(30):
        if ev: break
        time.sleep(0.1)
    assert 'ok' in ev, f'{name} no entró'
    players[name] = {'c': c, 'team': ev['ok'].get('team_id')}
    assert ev['ok'].get('team_id') == expected_team[i], \
        f'{name}: equipo {ev["ok"].get("team_id")} != esperado {expected_team[i]}'
    print(f'{name} → equipo {ev["ok"].get("team_id")} ({got["teams"][ev["ok"]["team_id"]]["nombre"] if ev["ok"].get("team_id") is not None else "?"})')

# esperar update_teams con miembros
time.sleep(1)
ut = got.get('teams')
counts = [len(t['members']) for t in ut]
assert sorted(counts) == [1, 1, 1, 2, 2] or sorted(counts) == [0,0,1,1,2] or max(counts)-min(counts) <= 1, \
    f'reparto desequilibrado: {counts}'
print('miembros por equipo:', counts, '- equilibrado OK')

# responder (round 0 del quiz Demo, correcta=1)
got.pop('results', None)
host.emit('start_game')
for _ in range(40):
    if 'results' not in got and not got.get('no_more'):
        pass
    time.sleep(0.25)
    if got.get('results'): break
    # revelar a mano tras 5s si aún no
# esperar la pregunta
for _ in range(40):
    pass
# responder: Carlos/Luis/María correcta(t=1), Ana/Josué incorrecta(t=0)
pattern = [1, 0, 1, 1, 0]
for name, opt in zip(names, pattern):
    players[name]['c'].emit('submit_answer', {'option_index': opt})
time.sleep(1.5)
host.emit('show_results')
for _ in range(40):
    if 'results' in got: break
    time.sleep(0.25)
assert 'results' in got and got['results'].get('teams'), 'show_results sin teams'
teams_lb = got['results']['teams']
print('marcador tras Q0:', [(t['nombre'], t['score']) for t in teams_lb])
# Los equipos 0 (Carlos, María) y 2 (Luis) tenían aciertos → puntos > 0
scores = {t['id']: t['score'] for t in teams_lb}
assert scores[0] > 0 and scores[2] > 0 and scores[1] == 0, f'marcador inesperado: {scores}'
# orden descendente
ss = [t['score'] for t in teams_lb]
assert ss == sorted(ss, reverse=True), 'orden de equipos incorrecto'

# finalizar
got.pop('final', None)
host.emit('force_end_quiz')
for _ in range(30):
    if 'final' in got: break
    time.sleep(0.2)
final = got.get('final') or {}
assert final.get('teams'), 'game_over sin teams'
print('podio final de equipos:', [(t['nombre'], t['score']) for t in final['teams']])
print('COMPETENCIA POR EQUIPOS: TODO OK ✅')

for p in players.values(): p['c'].disconnect()
host.disconnect()
