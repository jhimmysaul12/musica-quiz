# 🎵 Música Quiz

Dinámica de preguntas en tiempo real tipo Kahoot para el grupo de alabanza.
Funciona **100% en red local** (laptop + Wi-Fi, sin internet ni servicios externos).

## Ejecutar

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

- **Anfitrión (tú, en el proyector):** http://localhost:5000/host
- **Jugadores (celulares):** http://TU-IP:5000/join  ← el QR y esta URL se muestran en la pantalla del host al crear la sala
- **Panel de preguntas:** http://localhost:5000/editor

La IP local se muestra en la consola al arrancar (ej. `http://192.168.1.10:5000/join`).

## Flujo de un juego

1. Anfitrión: `/host` → elige quiz → **Crear partida** → se genera un **PIN de 4 dígitos + QR**.
2. Jugadores: escanean el QR o van a `/join?pin=XXXX`, escriben su nombre (sin cuenta).
3. Anfitrión ve la lista de esperando jugadores en tiempo real y pulsa **Iniciar**.
4. Cada pregunta tiene su **temporizador animado**; el jugador responde tocando una de las 4 opciones.
5. Se revela la respuesta correcta, distribución de respuestas y **ranking animado**.
6. Al terminar: **podio** 🥇🥈🥉 con confetti y sonido de victoria.
7. Resultados exportados automáticamente a `scores/CSV`.

## Puntuación

- Respuesta correcta: hasta **1000 pts**, escalados por rapidez (tipo Kahoot).
- **Racha**: +100 por cada acierto consecutivo (hasta 5 niveles = +500 max).
- Todo el cálculo es **server-side**: el navegador nunca puntúa, y:
  - no se puede responder dos veces,
  - no se puede responder después del tiempo,
  - solo puntúa quien está en la sala correcta (PIN).

## Categorías

Música cristiana · Cantantes y grupos · Instrumentos · Biblia y música ·
Teoría musical · Alabanza y adoración · Cultura general musical

## Panel de preguntas

`/editor` permite crear/editar/eliminar quizzes y preguntas con:
texto, 4 alternativas, correcta, tiempo por pregunta, categoría, dificultad,
imagen opcional, explicación opcional, e **importación desde JSON o CSV**.

## Sonidos

Generados por Web Audio (sin archivos protegidos): tick del reloj, correcto,
incorrecto, nueva pregunta y victoria.

## Créditos

Arquitectura inspirada en [quiz_ufpa](https://github.com/lasseufpa/quiz_ufpa)
(Flask + Flask-SocketIO). Ver `CREDITS.md`. Uso interno no comercial.
