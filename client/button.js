/**
 * <praxis-record-button>
 *
 * Runder Aufnahme-Button mit drei Zonen:
 *  - Mitte:             Play/Stop umschalten  -> Event "toggle"      {detail: {playing, mode}}
 *  - obere Ringhälfte:  Modus durchschalten   -> Event "mode-change" {detail: {mode}}
 *                       (Beschriftung geprägt entlang der Rundung)
 *  - untere Ringhälfte: Einstellungen öffnen  -> Event "settings"
 *  - LED oben rechts:   grün, wenn Server erreichbar -> Event "server-status" {detail: {online}}
 *
 * Attribute:
 *  mode="psychotherapie" | "sprechstunde" | "direkt"   (blau | rot | grün), Default: psychotherapie
 *  playing                                   (boolesch, gesetzt = Aufnahme läuft)
 *  online                                    (boolesch, LED an – manuell oder per health-url)
 *  health-url="https://…/health"             (optional, wird regelmäßig per fetch geprüft)
 *  health-interval="5000"                    (ms, Default 5000, min. 1000)
 *  disabled
 *
 * CSS-Variablen:  --size (Default 220px), --c-label (Schriftfarbe, Default fast schwarz)
 *
 * Hinweis health-url: Der Endpunkt muss vom Browser aus erreichbar sein
 * (gleicher Origin oder CORS-Header), sonst bleibt die LED aus.
 */

const RING = 'M 8 120 A 112 112 0 0 1 232 120 L 198 120 A 78 78 0 0 0 42 120 Z';
// Grundlinie für die Beschriftung (Radius 89, damit die Schrift mittig im Ring sitzt)
const LABEL_ARC = 'M 31 120 A 89 89 0 0 1 209 120';
const LOWER = 'M 8 120 A 112 112 0 0 0 232 120 L 198 120 A 78 78 0 0 1 42 120 Z';

const MODES = [
  { id: 'psychotherapie', label: 'P-Therapie' },
  { id: 'sprechstunde',   label: 'S-Stunde' },
  { id: 'direkt',         label: 'Direkt' },
];

const TEMPLATE = /* html */ `
<style>
  :host {
    --size: 220px; --c-label: #0d0d0d;
    --c-light: #5b93ec; --c-main: #2a63c4; --c-dark: #143a7a; --c-deep: #0b2350; --c-ink: #eaf2ff;
    display: inline-block; width: var(--size); aspect-ratio: 1;
    -webkit-tap-highlight-color: transparent; user-select: none;
  }
  :host([mode="sprechstunde"]) {
    --c-light: #ec6b5b; --c-main: #c4332a; --c-dark: #7f1a14; --c-deep: #4a0c08; --c-ink: #fff0ee;
  }
  :host([mode="direkt"]) {
    --c-light: #63cf8f; --c-main: #229a58; --c-dark: #136137; --c-deep: #08381e; --c-ink: #ecfff3;
  }
  :host([disabled]) { opacity: .5; pointer-events: none; }
  svg { width: 100%; height: 100%; display: block; overflow: visible; }
  stop { transition: stop-color .4s ease; }
  .tint { transition: fill .4s ease, stroke .4s ease; }
  .hit { cursor: pointer; outline: none; transition: filter .15s; }
  .hit:hover { filter: brightness(1.08); }
  .hit:active { filter: brightness(.9); }
  .focus { fill: none; stroke: transparent; stroke-width: 3; transition: stroke .15s; }
  .hit:focus-visible .focus { stroke: var(--c-ink); }
  .icon { transform-origin: 120px 120px; transition: opacity .2s, transform .2s; }
  :host(:not([playing])) #stop,
  :host([playing]) #play { opacity: 0; transform: scale(.6); }
  .label {
    font: 700 15px/1 system-ui, -apple-system, "Segoe UI", sans-serif;
    letter-spacing: 1.2px; pointer-events: none;
  }
  .label-hi   { fill: #fff; fill-opacity: .38; }
  .label-main { fill: var(--c-label); }
  #led { pointer-events: none; }
  #led-core { transition: fill .3s; }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>

<svg viewBox="0 0 240 240" role="group" aria-label="Aufnahme-Steuerung">
  <defs>
    <radialGradient id="g-ring" cx="38%" cy="28%" r="85%">
      <stop offset="0"   style="stop-color: var(--c-light)"/>
      <stop offset=".55" style="stop-color: var(--c-main)"/>
      <stop offset="1"   style="stop-color: var(--c-dark)"/>
    </radialGradient>
    <radialGradient id="g-core" cx="38%" cy="32%" r="75%">
      <stop offset="0"   style="stop-color: var(--c-light)"/>
      <stop offset=".6"  style="stop-color: var(--c-main)"/>
      <stop offset="1"   style="stop-color: var(--c-dark)"/>
    </radialGradient>
    <linearGradient id="g-low" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0"   style="stop-color: var(--c-dark)"/>
      <stop offset="1"   style="stop-color: var(--c-deep)"/>
    </linearGradient>

    <!-- Schraffur wie in der Skizze -->
    <pattern id="hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-35)">
      <line x1="0" y1="0" x2="0" y2="7" stroke="#fff" stroke-opacity=".12" stroke-width="2"/>
    </pattern>

    <!-- Körnige Oberflächentextur -->
    <filter id="grain" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency=".85" numOctaves="2" stitchTiles="stitch" result="noise"/>
      <feColorMatrix in="noise" type="matrix"
        values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  .7 0 0 0 -.28" result="specks"/>
      <feComposite in="specks" in2="SourceGraphic" operator="in" result="clipped"/>
      <feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="clipped"/></feMerge>
    </filter>

    <!-- Prägedruck: leichte Kante oben dunkel, Bauch innen mit Lichtreflex -->
    <path id="label-arc" d="${LABEL_ARC}"/>
    <filter id="emboss" x="-5%" y="-20%" width="110%" height="140%">
      <feGaussianBlur in="SourceAlpha" stdDeviation=".8" result="blur"/>
      <feOffset in="blur" dx="0" dy="1" result="off"/>
      <feComposite in="SourceAlpha" in2="off" operator="out" result="topEdge"/>
      <feFlood flood-color="#fff" flood-opacity=".22"/>
      <feComposite in2="topEdge" operator="in" result="sheen"/>
      <feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="sheen"/></feMerge>
    </filter>

    <filter id="glow" x="-150%" y="-150%" width="400%" height="400%">
      <feGaussianBlur stdDeviation="3.5" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>

    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="4" stdDeviation="4" flood-opacity=".35"/>
    </filter>
  </defs>

  <!-- Sockel -->
  <circle class="tint" cx="120" cy="120" r="115" style="fill: var(--c-deep)" filter="url(#shadow)"/>

  <!-- Obere Ringhälfte: Modus wechseln -->
  <g id="mode-switch" class="hit" role="button" tabindex="0" aria-label="Modus wechseln">
    <path d="${RING}" fill="url(#g-ring)" filter="url(#grain)"/>
    <text class="label label-hi" dy="1.2">
      <textPath href="#label-arc" startOffset="50%" text-anchor="middle"></textPath>
    </text>
    <text class="label label-main" filter="url(#emboss)">
      <textPath href="#label-arc" startOffset="50%" text-anchor="middle"></textPath>
    </text>
    <path class="focus" d="${RING}"/>
  </g>

  <!-- Untere Ringhälfte: Einstellungen -->
  <g id="settings" class="hit" role="button" tabindex="0" aria-label="Einstellungen öffnen">
    <path d="${LOWER}" fill="url(#g-low)" filter="url(#grain)"/>
    <path d="${LOWER}" fill="url(#hatch)"/>
    <g class="tint" transform="translate(120 215)" style="stroke: var(--c-ink)" fill="none" opacity=".85">
      <circle r="7.5" stroke-width="4" stroke-dasharray="3 2.89"/>
      <circle r="5" stroke-width="3"/>
    </g>
    <path class="focus" d="${LOWER}"/>
  </g>

  <!-- Trennstriche links/rechts -->
  <g class="tint" style="stroke: var(--c-deep)" stroke-width="3" stroke-linecap="round">
    <line x1="2" y1="120" x2="44" y2="120"/>
    <line x1="196" y1="120" x2="238" y2="120"/>
  </g>

  <!-- Fuge um die Mitte -->
  <circle class="tint" cx="120" cy="120" r="78" style="fill: var(--c-deep)"/>

  <!-- Mitte: Play/Stop -->
  <g id="core" class="hit" role="button" tabindex="0" aria-pressed="false" aria-label="Aufnahme starten">
    <circle cx="120" cy="120" r="72" fill="url(#g-core)" filter="url(#grain)"/>
    <circle cx="120" cy="120" r="71" fill="none" stroke="#fff" stroke-opacity=".18" stroke-width="1.5"/>
    <path id="play" class="icon tint" d="M106 93 L148 120 L106 147 Z"
          style="fill: var(--c-ink); stroke: var(--c-ink)" stroke-width="6" stroke-linejoin="round"/>
    <rect id="stop" class="icon tint" x="97" y="97" width="46" height="46" rx="6"
          style="fill: var(--c-ink)"/>
    <circle class="focus" cx="120" cy="120" r="75"/>
  </g>

  <!-- LED oben rechts -->
  <g id="led" role="img" aria-label="Server nicht erreichbar">
    <circle class="tint" cx="187" cy="53" r="10" style="fill: var(--c-deep)"/>
    <circle id="led-core" cx="187" cy="53" r="6.5" fill="#2b3530"/>
    <circle cx="185" cy="51" r="2" fill="#fff" opacity=".45"/>
  </g>
</svg>
`;

class PraxisRecordButton extends HTMLElement {
  static observedAttributes = ['mode', 'playing', 'online', 'health-url', 'health-interval'];

  #timer = null;
  #abort = null;

  constructor() {
    super();
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = TEMPLATE;
    this.$core = root.getElementById('core');
    this.$settings = root.getElementById('settings');
    this.$modeSwitch = root.getElementById('mode-switch');
    this.$labels = root.querySelectorAll('.label textPath');
    this.$led = root.getElementById('led');
    this.$ledCore = root.getElementById('led-core');

    const bind = (el, fn) => {
      el.addEventListener('click', fn);
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); }
      });
    };
    bind(this.$core, () => this.toggle());
    bind(this.$modeSwitch, () => this.cycleMode());
    bind(this.$settings, () => this.#emit('settings', { mode: this.mode }));
  }

  connectedCallback() {
    if (!this.hasAttribute('mode')) this.setAttribute('mode', 'psychotherapie');
    this.#render();
    this.#startHealth();
  }

  disconnectedCallback() { this.#stopHealth(); }

  attributeChangedCallback(name) {
    if ((name === 'health-url' || name === 'health-interval') && this.isConnected) this.#startHealth();
    this.#render();
  }

  get mode() {
    const m = this.getAttribute('mode');
    return MODES.some((x) => x.id === m) ? m : 'psychotherapie';
  }
  set mode(v) { this.setAttribute('mode', v); }
  get playing() { return this.hasAttribute('playing'); }
  set playing(v) { this.toggleAttribute('playing', !!v); }
  get online() { return this.hasAttribute('online'); }
  set online(v) { this.toggleAttribute('online', !!v); }

  cycleMode() {
    const i = MODES.findIndex((x) => x.id === this.mode);
    this.mode = MODES[(i + 1) % MODES.length].id;
    this.#emit('mode-change', { mode: this.mode });
  }

  toggle() {
    this.playing = !this.playing;
    this.#emit('toggle', { playing: this.playing, mode: this.mode });
  }

  #emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
  }

  #render() {
    const p = this.playing;
    this.$core.setAttribute('aria-pressed', String(p));
    this.$core.setAttribute('aria-label', p ? 'Aufnahme stoppen' : 'Aufnahme starten');

    const label = MODES.find((x) => x.id === this.mode).label;
    this.$labels.forEach((t) => { t.textContent = label; });
    this.$modeSwitch.setAttribute('aria-label', `Modus wechseln, aktuell ${label}`);

    const on = this.online;
    this.$ledCore.setAttribute('fill', on ? '#3ddc6a' : '#2b3530');
    if (on) this.$ledCore.setAttribute('filter', 'url(#glow)');
    else this.$ledCore.removeAttribute('filter');
    this.$led.setAttribute('aria-label', on ? 'Server erreichbar' : 'Server nicht erreichbar');
  }

  #startHealth() {
    this.#stopHealth();
    const url = this.getAttribute('health-url');
    if (!url) return;
    const interval = Math.max(1000, Number(this.getAttribute('health-interval')) || 5000);

    const check = async () => {
      this.#abort?.abort();
      const ac = (this.#abort = new AbortController());
      const timeout = setTimeout(() => ac.abort(), Math.min(3000, interval - 200));
      let ok = false;
      try {
        const res = await fetch(url, { cache: 'no-store', signal: ac.signal });
        ok = res.ok;
      } catch { ok = false; }
      finally { clearTimeout(timeout); }
      if (ac !== this.#abort) return; // von neuerer Prüfung überholt
      if (ok !== this.online) {
        this.online = ok;
        this.#emit('server-status', { online: ok });
      }
    };

    check();
    this.#timer = setInterval(check, interval);
  }

  #stopHealth() {
    clearInterval(this.#timer);
    this.#timer = null;
    this.#abort?.abort();
    this.#abort = null;
  }
}

if (!customElements.get('praxis-record-button')) {
  customElements.define('praxis-record-button', PraxisRecordButton);
}

export { PraxisRecordButton };