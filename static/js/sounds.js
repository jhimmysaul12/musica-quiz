/* MÚSICA QUIZ — sonidos con Web Audio (bis) + música ambiental.
   Generados localmente (sin archivos protegidos en el repo);
   la música de espera/victoria se carga desde /static/sounds (la
   sube el anfitrión en el editor). */
const SFX = (() => {
  let ctx = null;
  function ensure() {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (e) { return null; }
    }
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  }
  function tone(freq, dur, type = 'sine', when = 0, gain = 0.18) {
    const a = ensure(); if (!a) return;
    const o = a.createOscillator(), g = a.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(gain, a.currentTime + when);
    g.gain.exponentialRampToValueAtTime(0.001, a.currentTime + when + dur);
    o.connect(g).connect(a.destination);
    o.start(a.currentTime + when);
    o.stop(a.currentTime + when + dur + 0.05);
  }
  return {
    tick()  { tone(880, 0.07, 'square', 0, 0.10); },
    correct() { tone(523, .12, 'triangle'); tone(659, .12, 'triangle', .1); tone(784, .2, 'triangle', .2); },
    wrong() { tone(220, .25, 'sawtooth', 0, .12); tone(180, .35, 'sawtooth', .12, .12); },
    newQuestion() { tone(392, .1, 'triangle'); tone(523, .15, 'triangle', .08); },
    victory() {
      [523, 659, 784, 1047, 784, 1047].forEach((f, i) =>
        tone(f, .18, 'triangle', i * .15, .2));
    },
    timeWarning() { tone(300, .1, 'square', 0, .12); tone(300, .1, 'square', .15, .12); },
  };
})();

// Ticks de los últimos 5 segundos en pantallas de jugador y host
function bindCountdownTimer(secondsLeft, total) {
  let s = secondsLeft;
  const warnedAt = [5, 4];
  const iv = setInterval(() => {
    s--;
    if (warnedAt.includes(s) || s === 3) SFX.timeWarning();
    if (s <= 0) clearInterval(iv);
  }, 1000);
  return iv;
}

/* ---- Música ambiental: espera / victoria ----
   Urls servidas por /api/ambient; el navegador cachea una copia.
   Solo suena tras un gesto del usuario (política de autoplay). */
const Ambient = (() => {
  const audioEl = new Audio();
  audioEl.loop = true;
  audioEl.volume = 0.35;
  let currentSlot = null;
  let urls = {};

  async function refresh() {
    try {
      const r = await fetch('/api/ambient');
      urls = await r.json() || {};
    } catch (e) { urls = {}; }
  }
  refresh();

  return {
    async play(slot) {
      if (!urls[slot]) await refresh();
      const url = urls[slot];
      if (!url) return;  // sin música configurada: silencio elegante
      if (currentSlot === slot && !audioEl.paused) return;
      if (audioEl.src !== location.origin + url) audioEl.src = url;
      audioEl.loop = (slot === 'espera');  // la victoria sonó UNA vez y termina
      try { await audioEl.play(); currentSlot = slot; } catch (e) {}
    },
    stop() {
      try { audioEl.pause(); audioEl.currentTime = 0; } catch (e) {}
      currentSlot = null;
    },
    refresh,
  };
})();
