"""
MÚSICA QUIZ — Dinámica de preguntas en tiempo real para el grupo de alabanza
============================================================================
Basado en la arquitectura de quiz_ufpa (https://github.com/lasseufpa/quiz_ufpa),
inspiración en su flujo host/jugador con Flask + Flask-SocketIO.
Ampliado para Música Quiz: salas con PIN, QR, timer, puntuación Kahoot
(velocidad + racha), categorías, sonidos y lanzamiento en red local.

USO INTERNO NO COMERCIAL. Ver CREDITS.md.
"""
import os
import json
import csv
import secrets
import hashlib
import time
import socket
import qrcode
import threading
from datetime import datetime
from pathlib import Path

from flask import (Flask, render_template, request, session, jsonify,
                   redirect, url_for, flash, send_from_directory)
from flask_socketio import SocketIO, emit, join_room, leave_room

BASE_DIR = Path(__file__).parent
QUIZZES_FOLDER = 'static/quizzes'
UPLOAD_FOLDER = 'static/quiz-figures'
SECRET_FOLDER = '.private'
SCORES_FOLDER = 'scores'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
DEFAULT_QUESTION_TIME = 20  # segundos

os.makedirs(QUIZZES_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SCORES_FOLDER, exist_ok=True)
os.makedirs(SECRET_FOLDER, exist_ok=True)

# ------------- Claves de sesión (auto-generadas si no existen) -------------
secretpath = BASE_DIR / SECRET_FOLDER / ".secret"
_DEFAULT_FLASK_PW_HASH = hashlib.sha256(b"servir123").hexdigest()
_DEFAULT_ADMIN_PW_HASH = hashlib.sha256(b"admin123").hexdigest()


def _hash_password(name: str) -> str:
    import getpass
    s = getpass.getpass(f"Crear contraseña de {name}: ")
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


if not secretpath.exists():
    # En la nube no hay terminal para getpass: usar contraseñas por defecto
    # (defínalas con FLASK_PW / ADMIN_PW vía variables de entorno si desea otras)
    import os as _os2
    _f = _os2.environ.get("FLASK_PW", "servir123")
    _a = _os2.environ.get("ADMIN_PW", "admin123")
    with open(secretpath, "w+") as f:
        f.write(hashlib.sha256(_f.encode()).hexdigest() + "\n")
        f.write(hashlib.sha256(_a.encode()).hexdigest() + "\n")
elif len(open(secretpath).readlines()) not in (1, 2):
    print("archivo .secret inválido"); exit(1)

app = Flask(__name__)
with open(secretpath) as f:
    lines = [x.strip() for x in f.readlines() if x.strip()]
app.config['SECRET_KEY'] = lines[0]
ADMIN_PASSWORD = lines[1] if len(lines) > 1 else None

# threading en vez de eventlet: compatible con Windows/Python moderno
socketio = SocketIO(
    app,
    async_mode='threading',
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25,
    cors_allowed_origins="*",
    max_http_buffer_size=1e7
)

# ============================ Estado global ============================
STATE_LOBBY = 0
STATE_QUESTION = 1
STATE_ANSWER = 2
STATE_GAMEOVER = 3

# Una sesion activa por PIN: {pin: GameState}
GAMES = {}
PLAYER_TO_GAME = {}          # socket sid -> pin de su partida


class GameState:
    """Estado de UNA partida (por PIN). El servidor es la fuente única de verdad."""

    def __init__(self, quiz_name: str, host_sid: str):
        self.host_sid = host_sid
        self.quiz_name = quiz_name
        self.players = {}       # sid -> {nickname, avatar, score, streak, last_pts}
        self.answers = {}       # sid -> option_index
        self.answer_times = {}  # sid -> segundos restantes al responder
        self.current_question = -1
        self.state = STATE_LOBBY
        self.question_deadline = None
        self.timer_finished = False
        self.quiz = load_quiz_data(quiz_name)
        self.tokens = {}        # session_token -> sid
        self.teams = {}         # team_id (int) -> {nombre, color, puntos}
        self.player_team = {}   # sid -> team_id
        self.n_teams = 0        # 0 = sin equipos (individual)

    def set_teams(self, n: int):
        """Activar equipos (2-6) — solo en lobby."""
        if n < 2 or n > 6:
            return False
        self.n_teams = n
        colors = ['#e11d48', '#2563eb', '#eab308', '#16a34a', '#f97316', '#7c3aed']
        names = ['Roja', 'Azul', 'Amarilla', 'Verde', 'Naranja', 'Violeta']
        self.teams = {i: {'nombre': f'Equipo {names[i]}', 'color': colors[i], 'score': 0}
                      for i in range(n)}
        # reparto equilibrado round-robin de los ya presentes
        for i, sid in enumerate(self.players.keys()):
            team_id = i % n
            self.player_team[sid] = team_id
        return True

    def team_of(self, sid):
        return self.player_team.get(sid)

    def teams_leaderboard(self):
        rows = [{'id': t, **data} for t, data in self.teams.items()]
        rows.sort(key=lambda x: x['score'], reverse=True)
        return rows

    def teams_state(self):
        """Estado de equipos con miembros — para el rejunte del lobby."""
        self.team_scores()
        rows = []
        for t, data in self.teams.items():
            members = [{'nickname': p['nickname'], 'avatar': p.get('avatar', '🦁')}
                       for sid, p in self.players.items()
                       if self.player_team.get(sid) == t]
            rows.append({'id': t, **data, 'members': members})
        rows.sort(key=lambda x: x['score'], reverse=True)
        return rows

    def team_scores(self):
        """Recalcular el score de cada equipo como la suma de sus jugadores."""
        totals = {t: 0 for t in self.teams}
        for sid, p in self.players.items():
            t = self.player_team.get(sid)
            if t is not None and t in totals:
                totals[t] += p['score']
        for t, total in totals.items():
            self.teams[t]['score'] = total
        return totals

    def leaderboard(self):
        lb = [{'nickname': p['nickname'], 'avatar': p.get('avatar', '🦁'),
               'score': p['score'], 'streak': p['streak']}
              for p in self.players.values()]
        lb.sort(key=lambda x: x['score'], reverse=True)
        return lb

    def players_list(self):
        """Lista de jugadores con avatar, ordenada por puntuación (en juego)."""
        pl = [{'nickname': p['nickname'], 'avatar': p.get('avatar', '🦁'),
               'score': p['score'], 'streak': p.get('streak', 0)}
              for p in self.players.values()]
        pl.sort(key=lambda x: x['score'], reverse=True)
        return pl

    def reset_round(self):
        self.answers = {}
        self.answer_times = {}
        self.timer_finished = False


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


# ============================ Carga de quizzes ============================
def load_quiz_data(file):
    """Carga el JSON del quiz desde static/quizzes/{file}.json."""
    filename = BASE_DIR / QUIZZES_FOLDER / f"{file}.json"
    try:
        with open(filename, encoding='utf-8') as f:
            data = json.load(f)
        if 'title' not in data or 'questions' not in data:
            raise ValueError("Formato inválido de quiz")
        # completa campos opcionales
        for q in data['questions']:
            q.setdefault('time', DEFAULT_QUESTION_TIME)
            q.setdefault('category', 'General')
            q.setdefault('difficulty', 'media')
            q.setdefault('image', 'none')
            q.setdefault('explanation', '')
        return data
    except FileNotFoundError:
        print(f"Quiz '{file}' no encontrado")
        return None

# ============================ Rutas HTTP ============================


@app.route('/')
def index():
    """Raíz: elijo si es jugador o muestro el hub principal."""
    local_ip = get_local_ip()
    return render_template('index.html', host_ip=local_ip)


@app.route('/join')
def player_join_page():
    return render_template('player.html')


@app.route('/host')
def host_new_game():
    # El anfitrión necesita contraseña (equivale al acceso admin)
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login', next='host'))
    local_ip = get_local_ip()
    return render_template('host_new.html', host_ip=local_ip)


@app.route('/host/<pin>')
def host_view(pin):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login', next=f'host/{pin}'))
    local_ip = get_local_ip()
    return render_template('host.html', pin=pin, host_ip=local_ip)


@app.route('/editor')
def editor_view():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login', next='editor'))
    return render_template('editor.html')


HOST_SESSION_LIFETIME = 60 * 60 * 12  # 12 horas


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if hashlib.sha256(password.encode('utf-8')).hexdigest() == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            session.permanent = True
            app.permanent_session_lifetime = HOST_SESSION_LIFETIME
            nxt = request.form.get('next', '')
            if nxt.startswith('host') or nxt == 'editor':
                return redirect('/' + nxt)
            return redirect(url_for('admin_view'))
        flash('Contraseña incorrecta', 'error')
    return render_template('admin_login.html',
                           next=request.args.get('next', ''))


@app.route('/admin')
def admin_view():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    return render_template('admin.html')


@app.route('/api/pin/<pin>/qr')
def api_pin_qr(pin):
    """QR que apunta a la URL pública de /join (o IP local en desarrollo)."""
    # en la nube: usar el Host del request (ej. musica-quiz-3.onrender.com);
    # en red local (puerto 5000): usar la IP LAN como antes
    if request.host.endswith('onrender.com'):
        join_url = f"https://{request.host}/join?pin={pin}"
    else:
        join_url = f"http://{get_local_ip()}:5000/join?pin={pin}"
    img = qrcode.make(join_url)
    import io
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    from flask import Response
    return Response(buf.read(), mimetype='image/png')


@app.route('/api/quizzes', methods=['GET'])
def api_list_quizzes():
    quizzes = [f[:-5] for f in os.listdir(QUIZZES_FOLDER) if f.endswith('.json')]
    return jsonify(sorted(quizzes))


@app.route('/api/quiz/<path:quiz_name>', methods=['GET'])
def api_get_quiz(quiz_name):
    quiz = load_quiz(quiz_name)
    if quiz is None:
        return jsonify({'error': 'No encontrado'}), 404
    return jsonify(quiz)


@app.route('/api/quiz/<path:quiz_name>', methods=['POST'])
def api_save_quiz(quiz_name):
    data = request.get_json()
    if not data or 'title' not in data or 'questions' not in data:
        return jsonify({'error': 'Datos inválidos'}), 400
    save_quiz(quiz_name, data)
    return jsonify({'message': 'Quiz guardado'})


@app.route('/api/quiz/<path:quiz_name>', methods=['DELETE'])
def api_delete_quiz(quiz_name):
    path = BASE_DIR / QUIZZES_FOLDER / f"{quiz_name}.json"
    if not path.exists():
        return jsonify({'error': 'No encontrado'}), 404
    os.remove(path)
    return jsonify({'message': 'Eliminado'})


@app.route('/api/import_quiz/<path:quiz_name>', methods=['POST'])
def api_import_quiz(quiz_name):
    """Importa preguntas desde JSON o CSV y las agrega al quiz indicado."""
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Sin archivo'}), 400
    name = f.filename or ''
    questions = []
    try:
        if name.lower().endswith('.json'):
            raw = json.loads(f.read().decode('utf-8'))
            questions = raw if isinstance(raw, list) else raw.get('questions', [])
        elif name.lower().endswith(('.csv', '.tsv')):
            import io
            text = f.read().decode('utf-8-sig')
            delim = ';' if name.lower().endswith('.csv') else '\t'
            sniffer = csv.Sniffer()
            try:
                dialect = sniffer.sniff(text[:2048], delimiters=',;\t')
                delim = dialect.delimiter
            except Exception:
                pass
            reader = csv.DictReader(io.StringIO(text), delimiter=delim)
            cols = {c.strip().lower(): c for c in (reader.fieldnames or [])}
            for row in reader:
                opts = [row.get(cols.get(k, k) or k, '')
                        for k in ('option1', 'option2', 'option3', 'option4')]
                opts = [o for o in opts if o is not None and o != '']
                q = {
                    'text': row.get(cols.get('text', 'text') or 'text', ''),
                    'options': opts,
                    'correct_option': int(row.get(cols.get('correct_option', 'correct_option') or 'correct_option', 0)),
                    'time': int(row.get('time', DEFAULT_QUESTION_TIME)),
                    'category': row.get('category', 'General'),
                    'difficulty': row.get('difficulty', 'media'),
                    'explanation': row.get('explanation', ''),
                    'image': 'none',
                }
                questions.append(q)
        else:
            return jsonify({'error': 'Formato no soportado (usa .json o .csv)'}), 400
    except Exception as e:
        return jsonify({'error': f'Error parseando: {e}'}), 400

    valid = []
    for q in questions:
        if not q.get('text') or not q.get('options'):
            continue
        q.setdefault('time', DEFAULT_QUESTION_TIME)
        q.setdefault('category', 'General')
        q.setdefault('difficulty', 'media')
        q.setdefault('explanation', '')
        q.setdefault('image', 'none')
        valid.append(q)

    existing = load_quiz(quiz_name) or {'title': quiz_name, 'questions': []}
    existing.setdefault('title', quiz_name)
    existing['questions'].extend(valid)
    save_quiz(quiz_name, existing)
    return jsonify({'message': f'{len(valid)} preguntas importadas',
                    'quiz': existing})


@app.route('/api/upload_image', methods=['POST'])
def api_upload_image():
    if 'image' not in request.files:
        return jsonify({'error': 'Sin imagen'}), 400
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'Sin archivo'}), 400
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': 'Formato no permitido'}), 400
    unique = hashlib.md5(f"{time.time()}{file.filename}".encode()).hexdigest()
    filename = f"{unique}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    return jsonify({'filename': filename, 'url': f'{UPLOAD_FOLDER}/{filename}'})


AUDIO_FOLDER = 'static/quiz-audio'
AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'aac', 'opus', 'webm'}
os.makedirs(AUDIO_FOLDER, exist_ok=True)

# Música ambiental (espera/victoria) gestionada desde el editor
AMBIENT_FILE = BASE_DIR / '.private' / 'ambient.json'
AMBIENT_SLOTS = ('espera', 'victoria')


def load_ambient():
    try:
        with open(AMBIENT_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_ambient(data):
    with open(AMBIENT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _Ambient_url(slot):
    data = load_ambient()
    fname = data.get(slot)
    return f'/static/sounds/{fname}' if fname else None


@app.route('/api/ambient', methods=['GET'])
def api_ambient_get():
    d = load_ambient()
    return jsonify({slot: (f'/static/sounds/{d[slot]}' if d.get(slot) else None)
                    for slot in AMBIENT_SLOTS})


@app.route('/api/ambient/<slot>', methods=['POST', 'DELETE'])
def api_ambient_set(slot):
    if slot not in AMBIENT_SLOTS:
        return jsonify({'error': f'Slot inválido: usa {", ".join(AMBIENT_SLOTS)}'}), 400
    if request.method == 'DELETE':
        d = load_ambient()
        d.pop(slot, None)
        save_ambient(d)
        return jsonify({'message': f'Música de {slot} eliminada'})
    if 'audio' not in request.files:
        return jsonify({'error': 'Sin archivo de audio'}), 400
    file = request.files['audio']
    name = file.filename or ''
    ext = name.rsplit('.', 1)[1].lower() if '.' in name else ''
    if ext not in AUDIO_EXTENSIONS:
        return jsonify({'error': f'Formato no permitido: usa {", ".join(sorted(AUDIO_EXTENSIONS))}'}), 400
    fname = f'ambient_{slot}.{ext}'
    file.save(BASE_DIR / 'static' / 'sounds' / fname)
    d = load_ambient()
    d[slot] = fname
    save_ambient(d)
    return jsonify({'message': f'Música de {slot} actualizada',
                    'url': f'/static/sounds/{fname}'})


@app.route('/api/upload_audio', methods=['POST'])
def api_upload_audio():
    """Sube un audio (mp3/wav/ogg/m4a...) para usar como pregunta."""
    if 'audio' not in request.files:
        return jsonify({'error': 'Sin audio'}), 400
    file = request.files['audio']
    if file.filename == '':
        return jsonify({'error': 'Sin archivo'}), 400
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if ext not in AUDIO_EXTENSIONS:
        return jsonify({'error': f'Formato no permitido: usa {", ".join(sorted(AUDIO_EXTENSIONS))}'}), 400
    unique = hashlib.md5(f"{time.time()}{file.filename}".encode()).hexdigest()
    filename = f"{unique}.{ext}"
    file.save(os.path.join(AUDIO_FOLDER, filename))
    return jsonify({'filename': filename, 'url': f'{AUDIO_FOLDER}/{filename}'})


# ============================ Utilidades ============================


def load_quiz(quiz_name):
    path = BASE_DIR / QUIZZES_FOLDER / f"{quiz_name}.json"
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_quiz(quiz_name, data):
    path = BASE_DIR / QUIZZES_FOLDER / f"{quiz_name}.json"
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def export_scores_csv(game: GameState):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = BASE_DIR / SCORES_FOLDER / f"scores_{game.quiz_name}_{timestamp}.csv"
    os.makedirs(fname.parent, exist_ok=True)
    with open(fname, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Jugador', 'Puntuación', 'Racha máx'])
        lb = game.leaderboard()
        for row in lb:
            w.writerow([row['nickname'], row['score'], row['streak']])
    print(f"Resultados exportados: {fname}")


# ============================ Puntuación Kahoot ============================
MAX_POINTS = 1000
STREAK_BONUS_POINTS = 100    # bono fijo por racha creciente


def compute_points(seconds_left: float, total_time: float) -> int:
    """
    Fórmula tipo Kahoot: 1000 * (1 - (t_respuesta/2) / t_total)
    server-side: el navegador NUNCA puntúa.
    """
    if total_time <= 0:
        return MAX_POINTS
    spent = max(0.0, total_time - max(0.0, seconds_left))
    ratio = 1.0 - (spent / 2.0) / total_time
    return max(1, int(MAX_POINTS * ratio))


# ============================ Timer por pregunta ============================
def _timer_thread(pin: str, q_time: int):
    """Cierra automáticamente la ronda cuando se agota el tiempo."""
    time.sleep(q_time)
    game = GAMES.get(pin)
    if game and game.state == STATE_QUESTION:
        game.timer_finished = True
        socketio.start_background_task(reveal_results, pin, auto=True)
        socketio.emit('time_up', {}, to=pin)


@app.route('/api/avatars')
def api_avatars():
    """Lista de animales bíblicos disponibles para elegir como avatar."""
    return jsonify(BIBLE_ANIMALS)


# ============================ Eventos SocketIO ============================
@socketio.on('connect')
def on_connect():
    print(f"Cliente conectado: {request.sid}")


@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    pin = PLAYER_TO_GAME.pop(sid, None)
    if pin and pin in GAMES:
        game = GAMES[pin]
        if sid == game.host_sid:
            # Host cerró: emitir fin y limpiar
            lb = game.leaderboard()
            socketio.emit('game_over', lb, to=pin)
            export_scores_csv(game)
            GAMES.pop(pin, None)
            for tk, s2 in list(game.tokens.items()):
                PLAYER_TO_GAME.pop(s2, None)
            print(f"Host cerró la partida {pin}")
        elif sid in game.players:
            nick = game.players[sid]['nickname']
            del game.players[sid]
            if sid in game.answers:
                del game.answers[sid]
            emit('player_left', {'nickname': nick}, to=pin)
            socketio.emit('update_player_list',
                          game.players_list(), to=pin)
            print(f"Jugador {nick} salió de {pin}")


@socketio.on('create_game')
def on_create_game(data):
    """Host crea una partida con un quiz -> devuelve PIN."""
    quiz_name = (data or {}).get('quiz-name')
    if not quiz_name:
        emit('create_failed', {'reason': 'Falta quiz'})
        return
    quiz = load_quiz_data(quiz_name)
    if quiz is None:
        emit('create_failed', {'reason': f'Quiz "{quiz_name}" no existe'})
        return
    for _ in range(10):
        pin = str(secrets.randbelow(9000) + 1000)  # 4 dígitos
        if pin not in GAMES:
            break
    else:
        emit('create_failed', {'reason': 'No se pudo generar PIN'})
        return
    GAMES[pin] = GameState(quiz_name, request.sid)
    PLAYER_TO_GAME[request.sid] = pin
    join_room(pin)
    emit('game_created', {'pin': pin}, to=request.sid)
    print(f"Partida {pin} creada con quiz '{quiz_name}'")


@socketio.on('host_join')
def on_host_join(data):
    pin = (data or {}).get('pin')
    game = GAMES.get(pin)
    if game is None:
        emit('host_error', {'reason': 'Partida no existe'})
        return
    game.host_sid = request.sid
    PLAYER_TO_GAME[request.sid] = pin
    join_room(pin)
    emit('update_player_list', list(game.players.values()), to=pin)
    emit('host_ready', {'pin': pin,
                        'n_teams': game.n_teams,
                        'teams': game.teams_leaderboard()}, to=request.sid)


# Avatares bíblicos: animales reconocibles de la Escritura
BIBLE_ANIMALS = [
    {'emoji': '🦁', 'nombre': 'León'},
    {'emoji': '🐑', 'nombre': 'Oveja'},
    {'emoji': '🕊️', 'nombre': 'Paloma'},
    {'emoji': '🦅', 'nombre': 'Águila'},
    {'emoji': '🐍', 'nombre': 'Serpiente'},
    {'emoji': '🐟', 'nombre': 'Pez'},
    {'emoji': '🐐', 'nombre': 'Cabra'},
    {'emoji': '🫏', 'nombre': 'Asno'},
    {'emoji': '🐫', 'nombre': 'Camello'},
    {'emoji': '🐄', 'nombre': 'Buey'},
    {'emoji': '🐎', 'nombre': 'Caballo'},
    {'emoji': '🐝', 'nombre': 'Abeja'},
]
BIBLE_AVATARS = [a['emoji'] for a in BIBLE_ANIMALS]


@socketio.on('player_join')
def on_player_join(data):
    """Jugador entra con PIN, nombre y avatar (sin cuenta)."""
    pin = str((data or {}).get('pin', '')).strip()
    nickname = (data or {}).get('nickname', '').strip()[:20]
    avatar = (data or {}).get('avatar', '')[:8]
    if not nickname:
        return emit('join_failed', {'reason': 'Escribe tu nombre'})
    game = GAMES.get(pin)
    if game is None:
        return emit('join_failed', {'reason': 'PIN no válido'})
    if game.state != STATE_LOBBY:
        return emit('join_failed', {'reason': 'La partida ya empezó'})
    if avatar not in BIBLE_AVATARS:
        # asignación automática: primer animal libre para este jugador
        used = {p.get('avatar') for p in game.players.values()}
        avatar = next((a for a in BIBLE_AVATARS if a not in used), BIBLE_AVATARS[0])
    # mismo avatar no se repite dentro de la sala
    if any(p.get('avatar') == avatar for p in game.players.values()):
        used = {p.get('avatar') for p in game.players.values()}
        avatar = next((a for a in BIBLE_AVATARS if a not in used), avatar)
    existing = [p['nickname'].lower() for p in game.players.values()]
    if nickname.lower() in existing:
        return emit('join_failed', {'reason': 'Nombre ya en uso'})
    game.players[request.sid] = {
        'nickname': nickname,
        'avatar': avatar,
        'score': 0,
        'streak': 0,
        'last_pts': 0,
    }
    # asignar equipo si el modo equipos está activo (al más pequeño)
    if game.n_teams >= 2:
        counts = {t: 0 for t in game.teams}
        for t in game.player_team.values():
            counts[t] = counts.get(t, 0) + 1
        team_id = min(counts, key=lambda t: counts[t])
        game.player_team[request.sid] = team_id
    PLAYER_TO_GAME[request.sid] = pin
    join_room(pin)
    # token de reconexión
    token = secrets.token_hex(16)
    game.tokens[token] = request.sid
    emit('join_success', {'nickname': nickname, 'token': token,
                          'pin': pin,
                          'avatar': avatar,
                          'team_id': game.player_team.get(request.sid),
                          'n_teams': game.n_teams}, to=request.sid)
    socketio.emit('update_player_list', game.players_list(), to=pin)
    if game.n_teams >= 2:
        socketio.emit('update_teams', game.teams_state(), to=pin)
    print(f"Jugador '{nickname}' ({avatar}) entró a {pin}")


@socketio.on('setup_teams')
def on_setup_teams(data):
    """Host configura N equipos (2-6) o desactiva (0/None). Solo en lobby."""
    pin = PLAYER_TO_GAME.get(request.sid)
    game = GAMES.get(pin) if pin else None
    if game is None or request.sid != game.host_sid or game.state != STATE_LOBBY:
        return emit('teams_error', {'reason': 'Solo en lobby'})
    n = int((data or {}).get('n', 0) or 0)
    if n == 0:
        game.n_teams = 0
        game.teams = {}
        game.player_team = {}
    elif not game.set_teams(n):
        return emit('teams_error', {'reason': 'Equipos debe ser 2-6 (o 0 para individual)'})
    socketio.emit('update_teams', game.teams_state(), to=pin)
    if n == 0:
        socketio.emit('update_teams', [], to=pin)


@socketio.on('start_game')
def on_start_game():
    pin = PLAYER_TO_GAME.get(request.sid)
    game = GAMES.get(pin) if pin else None
    if game is None or request.sid != game.host_sid:
        return
    advance_question(pin)


@socketio.on('next_question')
def on_next_question():
    pin = PLAYER_TO_GAME.get(request.sid)
    game = GAMES.get(pin) if pin else None
    if game is None or request.sid != game.host_sid:
        return
    advance_question(pin)


def advance_question(pin: str):
    game = GAMES[pin]
    game.reset_round()
    game.current_question += 1
    q_index = game.current_question
    questions = game.quiz['questions']
    if q_index >= len(questions):
        lb = game.leaderboard()
        export_scores_csv(game)
        go_payload = {'individual': lb}
        if game.n_teams >= 2:
            game.team_scores()
            go_payload['teams'] = game.teams_leaderboard()
        socketio.emit('game_over', go_payload, to=pin)
        game.state = STATE_GAMEOVER
        return
    game.state = STATE_QUESTION
    q = questions[q_index]
    payload = {
        'text': q['text'],
        'options': q['options'],
        'time': q['time'],
        'category': q.get('category', 'General'),
        'difficulty': q.get('difficulty', 'media'),
        'image': (f"/{UPLOAD_FOLDER}/{q['image']}" if q['image'] != 'none' else ''),
        'audio': (f"/{AUDIO_FOLDER}/{q['audio']}" if q.get('audio') else ''),
        'question_index': q_index,
        'total_questions': len(questions),
    }
    # enviada a TODOS incluido host, pero sin la respuesta correcta
    socketio.emit('show_question', payload, to=pin)
    socketio.emit('update_answer_count',
                  {'answered': 0, 'total': len(game.players)}, to=pin)
    game.question_deadline = time.time() + q['time']
    threading.Thread(target=_timer_thread, args=(pin, q['time']),
                     daemon=True).start()


@socketio.on('submit_answer')
def on_submit_answer(data):
    pin = PLAYER_TO_GAME.get(request.sid)
    game = GAMES.get(pin) if pin else None
    if game is None or game.state != STATE_QUESTION:
        return emit('answer_rejected', {'reason': 'No estás en fase de pregunta'})
    if request.sid not in game.players:
        return emit('answer_rejected', {'reason': 'No eres jugador de esta sala'})
    if request.sid in game.answers:
        return emit('answer_rejected', {'reason': 'Ya respondiste'})
    if game.timer_finished or time.time() > game.question_deadline:
        return emit('answer_rejected', {'reason': 'Se acabó el tiempo'})
    try:
        option_index = int(data.get('option_index'))
    except (TypeError, ValueError):
        return emit('answer_rejected', {'reason': 'Respuesta inválida'})
    n_opts = len(game.quiz['questions'][game.current_question]['options'])
    if option_index < 0 or option_index >= n_opts:
        return emit('answer_rejected', {'reason': 'Índice fuera de rango'})
    seconds_left = max(0.0, game.question_deadline - time.time())
    game.answers[request.sid] = option_index
    game.answer_times[request.sid] = seconds_left
    emit('answer_received', to=request.sid)
    socketio.emit('update_answer_count',
                  {'answered': len(game.answers), 'total': len(game.players)},
                  to=pin)


def reveal_results(pin: str, auto=False):
    """Host muestra resultados (o auto al agotar timer)."""
    game = GAMES.get(pin)
    if game is None or game.state != STATE_QUESTION:
        return
    q_index = game.current_question
    if q_index < 0 or q_index >= len(game.quiz['questions']):
        return
    q = game.quiz['questions'][q_index]
    correct = int(q['correct_option'])
    total_time = q['time']
    distribution = [0] * len(q['options'])
    for ans in game.answers.values():
        if 0 <= int(ans) < len(distribution):
            distribution[int(ans)] += 1

    for sid, ans in game.answers.items():
        if sid not in game.players:
            continue
        if int(ans) == correct:
            pts = compute_points(game.answer_times.get(sid, 0), total_time)
            p = game.players[sid]
            p['streak'] += 1
            streak_bonus = min(p['streak'] - 1, 5) * STREAK_BONUS_POINTS
            p['score'] += pts + streak_bonus
            p['last_pts'] = pts + streak_bonus
            p['last_correct'] = True
        else:
            p = game.players[sid]
            p['streak'] = 0
            p['last_pts'] = 0
            p['last_correct'] = False

    game.state = STATE_ANSWER
    # respuesta correcta solo se revela AHORA
    result_payload = {
        'correct_option': correct,
        'correct_option_text': q['options'][correct],
        'explanation': q.get('explanation', ''),
        'distribution': distribution,
        'scores': {sid: p['score'] for sid, p in game.players.items()},
        'players': [{'nickname': p['nickname'], 'avatar': p.get('avatar', '🦁'),
                     'score': p['score'], 'streak': p.get('streak', 0),
                     'last_pts': p.get('last_pts', 0),
                     'last_correct': p.get('last_correct', False)}
                    for p in game.players.values()],
        'leaderboard': game.leaderboard()[:10],
    }
    if game.n_teams >= 2:
        game.team_scores()
        result_payload['teams'] = game.teams_leaderboard()
    socketio.emit('show_results', result_payload, to=pin)


@socketio.on('show_results')
def on_show_results():
    pin = PLAYER_TO_GAME.get(request.sid)
    game = GAMES.get(pin) if pin else None
    if game is None or request.sid != game.host_sid:
        return
    reveal_results(pin)


@socketio.on('force_end_quiz')
def on_force_end_quiz():
    pin = PLAYER_TO_GAME.get(request.sid)
    game = GAMES.get(pin) if pin else None
    if game is None or request.sid != game.host_sid:
        return
    lb = game.leaderboard()
    export_scores_csv(game)
    go_payload = {'individual': lb}
    if game.n_teams >= 2:
        game.team_scores()
        go_payload['teams'] = game.teams_leaderboard()
    socketio.emit('game_over', go_payload, to=pin)
    game.state = STATE_GAMEOVER
    game.current_question = -1
    game.answers = {}


# ============================ Main ============================
if __name__ == '__main__':
    print("=" * 50)
    print("  MÚSICA QUIZ — servidor iniciado")
    print("=" * 50)
    print(f"Host (panel inicial): http://localhost:5000/host")
    print(f"Editor:               http://localhost:5000/editor")
    print(f"Jugadores:            http://{get_local_ip()}:5000/join")
    print("=" * 50)
    import os as _os
    _port = int(_os.environ.get('PORT', 5000))
    print(f"Jugadores:            https://<TU-URL-RENDER>/join")
    print("=" * 50)
    if _os.environ.get('ON_RENDER'):
        socketio.run(app, host='0.0.0.0', port=_port)
    else:
        socketio.run(app, host='0.0.0.0', port=_port, debug=False,
                     allow_unsafe_werkzeug=True)
