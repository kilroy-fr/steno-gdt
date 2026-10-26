"""Der runde Steno-Knopf als Bild (Vorlage: client/button.js, aus Claude Desktop).

Drei Flaechen und zwei Lampen, Koordinaten wie in der Vorlage (viewBox 240×240):
- Mitte: ▶ / ■, geprägt wie die Beschriftung
- obere Ringhaelfte: Art des Gespraechs — Farbe des ganzen Knopfs und geprägte
  Beschriftung entlang der Rundung, ein Klick schaltet weiter
- untere Ringhaelfte (Zahnrad, geprägt wie ▶ / ■): Menue
- Lampe oben rechts: gruen = Server erreichbar; oben links orange = Fehler

Tk zeichnet unter Windows ohne Kantenglaettung und kennt weder Verlaeufe noch
Text auf einer Kurve. Gerechnet wird deshalb mit numpy/Pillow in vierfacher
Aufloesung und dann verkleinert; App haelt die fertigen Bilder je Zustand vor.

Ohne Schlagschatten: das Fenster ist rund, weil App die Hintergrundfarbe per
Farbschluessel durchsichtig schaltet — ein Schatten waere dort ein dunkler,
harter Ring um den Knopf.

Zwei Skins (SKINS), gleiche Flaechen und Lampen:
- klassisch: Verlaeufe, Koernung, gepraegte Zeichen (die Vorlage)
- t2med: flach in den Farben von T2med, gemessen am Programm (Funktionsleiste:
  Pastellkachel mit dunklem Zeichen; runde dunkelblaue Knoepfe mit weissem
  Zeichen) und an t2med.de (Dunkelblau #004f81, Gruen #179277). Sprechstunde
  ist dort blau statt rot: Rot heisst in T2med Warnung ("Schein fehlt").
  Der Aussenrand bleibt dunkel, sonst mischten sich die Kantenpixel mit dem
  Farbschluessel zu einem schwarzen Saum um einen hellen Knopf.
"""
import math
import threading
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

VIEW = 240
C = 120                      # Mittelpunkt
SS = 4                       # Ueberabtastung gegen Treppenkanten

# hell, Haupt, dunkel, tief, Zeichen
PALETTES = {
    "psycho":       ("#5b93ec", "#2a63c4", "#143a7a", "#0b2350", "#eaf2ff"),
    "sprechstunde": ("#ec6b5b", "#c4332a", "#7f1a14", "#4a0c08", "#fff0ee"),
    "direkt":       ("#63cf8f", "#229a58", "#136137", "#08381e", "#ecfff3"),
}
# tief (Rand, untere Haelfte), Ring, Mitte, Zeichen in der Mitte
T2MED_PALETTES = {
    "psycho":       ("#3b2384", "#7457c2", "#d6caec", "#300281"),   # Kachel "7"
    "sprechstunde": ("#034e7d", "#1b87cd", "#bbdcf6", "#003a67"),   # "JO/OK", "eEB beantragen", Kachel "Steno"
    "direkt":       ("#0b5a49", "#179277", "#d2ebe2", "#0b4a3c"),   # t2med.de
}
SKINS = {"klassisch": "Klassisch (Verlauf, geprägt)", "t2med": "T2med (flach, T2med-Farben)"}
DEFAULT_SKIN = "t2med"
LABELS = {"psycho": "P-Therapie", "sprechstunde": "S-Stunde", "direkt": "Direkt"}
LAMP, WARN_LAMP = (187, 53), (53, 53)
LAMP_ON, LAMP_OFF, LAMP_WARN = "#3ddc6a", "#2b3530", "#f29900"
T2MED_LAMP_OFF = "#8fa5b8"
LABEL_R = 89                 # Grundlinie der Beschriftung, mittig im Ring 78..112
LABEL_MAX_DEG = 32           # halbe Breite; ab ~39° saesse die Lampe im Weg
_lock = threading.Lock()     # App rendert vorab in einem Thread; FreeType-Schriften sind nicht threadfest


def zone(x: float, y: float, size: float) -> str | None:
    """Flaeche unter einem Punkt des Knopfbilds (Pixel): core | mode | menu | None."""
    u, v = x * VIEW / size - C, y * VIEW / size - C
    d = math.hypot(u, v)
    if d <= 75:
        return "core"
    if d <= 115:
        return "mode" if v < 0 else "menu"
    return None


def render(size: int, mode: str, playing: bool, online: bool, warn: bool = False,
           bg: str = "#1f2329", skin: str = DEFAULT_SKIN) -> Image.Image:
    """Knopf als RGB-Bild size×size auf Hintergrund bg; ausserhalb des Sockels
    (Radius 115 von 120) steht bg unveraendert."""
    with _lock:
        if skin == "klassisch":
            return _render(size, mode, playing, online, warn, bg)
        return _render_t2med(size, mode, playing, online, warn, bg)


def _render_t2med(size, mode, playing, online, warn, bg):
    """Flach: Ring oben in der Farbe der Art mit weisser Beschriftung, unten
    dunkel mit weissem Zahnrad (wie T2meds runde Knoepfe), Mitte als
    Pastellkachel mit dunklem ▶ / ■, dazwischen ein weisser Spalt."""
    deep, ring, core, symbol = (_rgb(c) for c in T2MED_PALETTES[mode])
    n = size * SS
    k = n / VIEW
    v, u = (np.mgrid[0:n, 0:n].astype(np.float32) + 0.5) / k
    d = np.hypot(u - C, v - C)
    img = np.empty((n, n, 3), np.float32)
    img[:] = _rgb(bg)

    _over(img, d <= 115, deep)
    _over(img, (d >= 78) & (d <= 112) & (v <= C), ring)
    _over(img, d <= 76, 255)
    _over(img, d <= 71, core)
    _over(img, (_stop if playing else _play)(u, v) <= 0, symbol)
    _over(img, _gear_mask(u, v), 255)

    for at, color, lit in ((LAMP, LAMP_ON if online else T2MED_LAMP_OFF, online),
                           *(((WARN_LAMP, LAMP_WARN, True),) if warn else ())):
        x, y = at
        dl = np.hypot(u - x, v - y)
        _over(img, dl <= 9, 255)
        _over(img, dl <= 7, _rgb(color))

    im = Image.fromarray(img.round().astype(np.uint8))
    _curved(im, k, LABELS[mode], (255, 255, 255), 1.0, LABEL_R, ("seguisb.ttf", "segoeuib.ttf"))
    return im.resize((size, size), Image.BOX)


def _render(size, mode, playing, online, warn, bg):
    light, main, dark, deep, _ = (_rgb(c) for c in PALETTES[mode])
    n = size * SS
    k = n / VIEW
    v, u = (np.mgrid[0:n, 0:n].astype(np.float32) + 0.5) / k
    d = np.hypot(u - C, v - C)
    img = np.empty((n, n, 3), np.float32)
    img[:] = _rgb(bg)
    grain = _grain(size)

    _over(img, d <= 115, deep)                                          # Sockel
    ring = (d >= 78) & (d <= 112)
    upper, lower = ring & (v <= C), ring & (v > C)
    # Verlaeufe wie objectBoundingBox in SVG: obere Haelfte 8..232 × 8..120
    t = np.hypot((u - (8 + 0.38 * 224)) / (0.85 * 224), (v - (8 + 0.28 * 112)) / (0.85 * 112))
    _over(img, upper, _ramp(t, ((0, light), (0.55, main), (1, dark))))
    _over(img, lower, _ramp((v - C) / 112, ((0, dark), (1, deep))))
    a = math.radians(35)
    p = np.mod(u * math.cos(a) - v * math.sin(a), 7)
    _over(img, lower & (np.minimum(p, 7 - p) <= 1), 255, 0.12)         # Schraffur
    _over(img, ring, 0, grain)
    sep = (np.abs(v - C) <= 1.5) & (((u >= 2) & (u <= 44)) | ((u >= 196) & (u <= 238))) & (d <= 115)
    _over(img, sep, deep)                                               # Trennstriche
    _over(img, d <= 78, deep)                                           # Fuge
    core = d <= 72
    t = np.hypot(u - (48 + 0.38 * 144), v - (48 + 0.32 * 144)) / (0.75 * 144)
    _over(img, core, _ramp(t, ((0, light), (0.6, main), (1, dark))))
    _over(img, core, 0, grain)
    _over(img, np.abs(d - 71) <= 0.75, 255, 0.18)

    _gear(img, u, v, light, main, deep)
    _symbol(img, u, v, playing, dark, deep)

    _lamp(img, u, v, k, LAMP, LAMP_ON if online else LAMP_OFF, online, deep)
    if warn:
        _lamp(img, u, v, k, WARN_LAMP, LAMP_WARN, True, deep)

    im = Image.fromarray(img.round().astype(np.uint8))
    _curved(im, k, LABELS[mode], (255, 255, 255), 0.38, LABEL_R - 1.2)  # Lichtkante darunter
    _curved(im, k, LABELS[mode], (13, 13, 13), 1.0, LABEL_R)
    return im.resize((size, size), Image.BOX)


def highlight(im: Image.Image, area: str, pressed: bool = False, warn: bool = False) -> Image.Image:
    """Flaeche unter der Maus aufhellen, gedrueckt abdunkeln (wie :hover/:active
    der Vorlage). Auf dem fertigen Bild: kostet fast nichts, render() bleibt je
    Zustand einmalig."""
    a = _areas(im.width, warn)[area][..., None]
    img = np.asarray(im, np.float32)
    img = img * (1 - a) + a * np.clip(img * (0.9 if pressed else 1.08), 0, 255)
    return Image.fromarray(img.round().astype(np.uint8))


@lru_cache(maxsize=4)
def _areas(size: int, warn: bool) -> dict:
    """Deckung der drei Flaechen je Bildpunkt, ohne die Lampen darauf."""
    n = size * SS
    v, u = (np.mgrid[0:n, 0:n].astype(np.float32) + 0.5) * VIEW / n
    d = np.hypot(u - C, v - C)
    ring = (d >= 78) & (d <= 112)
    lamps = np.hypot(u - LAMP[0], v - LAMP[1]) <= 10
    if warn:
        lamps |= np.hypot(u - WARN_LAMP[0], v - WARN_LAMP[1]) <= 10
    masks = {"core": d <= 75, "mode": ring & (v <= C) & ~lamps, "menu": ring & (v > C)}
    return {k: m.reshape(size, SS, size, SS).mean(axis=(1, 3), dtype=np.float32) for k, m in masks.items()}


# ------------------------------------------------------------ Bausteine
def _rgb(color: str) -> np.ndarray:
    return np.array([int(color[i:i + 2], 16) for i in (1, 3, 5)], np.float32)


def _over(img, mask, color, alpha=1.0):
    """color (Farbe oder Bild) mit Deckkraft mask × alpha ueber img legen —
    nur dort, wo etwas zu legen ist; die meisten Masken sind klein."""
    a = mask.astype(np.float32) * alpha
    sel = a > 0
    a = a[sel][:, None]
    if isinstance(color, np.ndarray) and color.ndim == 3:
        color = color[sel]
    img[sel] = img[sel] * (1 - a) + a * color


def _ramp(t, stops):
    t = np.clip(t, 0, 1)
    offsets = [o for o, _ in stops]
    return np.stack([np.interp(t, offsets, [c[i] for _, c in stops]) for i in range(3)], axis=-1)


@lru_cache(maxsize=4)
def _grain(size: int) -> np.ndarray:
    """Koernung wie feColorMatrix der Vorlage (Alpha = 0,7·Rauschen − 0,28), je
    Bildpunkt des fertigen Bilds — feiner wuerde sie beim Verkleinern verschwinden."""
    noise = np.random.default_rng(7).normal(0.5, 0.07, (size, size))
    return np.kron(np.clip(0.7 * noise - 0.28, 0, 1), np.ones((SS, SS))).astype(np.float32)


def _soft_disc(n, k, at, r, sigma) -> np.ndarray:
    im = Image.new("L", (n, n), 0)
    x, y = at[0] * k, at[1] * k
    ImageDraw.Draw(im).ellipse((x - r * k, y - r * k, x + r * k, y + r * k), fill=255)
    return np.asarray(im.filter(ImageFilter.GaussianBlur(sigma * k)), np.float32) / 255


def _segment(u, v, p, q):
    (x1, y1), (x2, y2) = p, q
    dx, dy = x2 - x1, y2 - y1
    t = np.clip(((u - x1) * dx + (v - y1) * dy) / (dx * dx + dy * dy), 0, 1)
    return np.hypot(u - x1 - t * dx, v - y1 - t * dy)


def _play(u, v):
    """Abstand zum Dreieck mit 6 breiter, runder Kontur (stroke-linejoin round);
    innen negativ."""
    pts = ((106, 93), (148, 120), (106, 147))
    inside = (u >= 106) & (np.abs(v - 120) <= (148 - u) * 27 / 42)
    edge = np.minimum.reduce([_segment(u, v, pts[i], pts[i - 1]) for i in range(3)])
    return np.where(inside, -edge, edge) - 3


def _stop(u, v):
    """Abstand zum Quadrat mit Rundung 6; innen negativ."""
    qx, qy = np.abs(u - 120) - 17, np.abs(v - 120) - 17
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - 6


def _symbol(img, u, v, playing, dark, deep):
    """▶ / ■ in die Mitte gepraegt wie die Beschriftung im Ring: in den Farben
    des Knopfs, dunkle Kontur, Lichtkante darunter."""
    shape = _stop if playing else _play
    sd = shape(u, v)
    _over(img, shape(u, v - 1.6) <= 0, 255, 0.38)                      # Lichtkante
    _over(img, sd <= 1.5, deep)                                         # Kontur
    _over(img, sd <= 0, _ramp((v - 93) / 54, ((0, deep), (0.45, dark), (1, dark))))


def _gear_mask(u, v):
    gd = np.hypot(u - 120, v - 215)
    arc = np.mod(np.arctan2(v - 215, u - 120), 2 * np.pi) * 7.5
    teeth = (np.abs(gd - 7.5) <= 2) & (np.mod(arc, 5.89) < 3)
    return teeth | (np.abs(gd - 5) <= 1.5)


def _gear(img, u, v, light, main, deep):
    """Zahnrad, gepraegt wie ▶ / ■ — heller als der Ring darunter, sonst
    verschwaende es im Dunkel der unteren Haelfte. Die Kontur ist die um 0,9
    in alle Richtungen verschobene Form: fuer die Zaehne gibt es keinen
    handlichen Abstand wie beim Dreieck."""
    near = (np.abs(u - 120) <= 14) & (np.abs(v - 215) <= 14)        # nur dort rechnen
    uu, vv = u[near], v[near]
    shift = [(0.9 * math.cos(a), 0.9 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
    for mask, color, alpha in (
            (_gear_mask(uu, vv - 1.4), 255, 0.38),                      # Lichtkante
            (np.logical_or.reduce([_gear_mask(uu + x, vv + y) for x, y in shift]), deep, 1.0),  # Kontur
            (_gear_mask(uu, vv), _ramp((vv - 205) / 20, ((0, light), (1, main))), 1.0)):
        full = np.zeros(near.shape, bool)
        full[near] = mask
        if isinstance(color, np.ndarray) and color.ndim == 2:
            ramp = np.zeros((*near.shape, 3), np.float32)
            ramp[near] = color
            color = ramp
        _over(img, full, color, alpha)


def _lamp(img, u, v, k, at, color, glow, housing):
    x, y = at
    d = np.hypot(u - x, v - y)
    _over(img, d <= 10, housing)
    if glow:                                   # feMerge: Unschaerfe doppelt, dann die Lampe
        halo = _soft_disc(img.shape[0], k, at, 6.5, 3.5)
        _over(img, 1 - (1 - halo) ** 2, _rgb(color))
    _over(img, d <= 6.5, _rgb(color))
    _over(img, np.hypot(u - x + 2, v - y + 2) <= 2, 255, 0.45)


@lru_cache(maxsize=8)
def _font(px: int, names: tuple = ("segoeuib.ttf",)):
    for name in (*names, "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            pass
    return ImageFont.load_default(px)


def _curved(im: Image.Image, k: float, text: str, color, alpha: float, r: float,
            fonts: tuple = ("segoeuib.ttf",)):
    """Text mittig oben entlang des Kreises mit Radius r, Grundlinie auf dem Kreis."""
    r *= k
    unit = 18                                  # Schriftgroesse in viewBox-Einheiten
    while True:
        font = _font(round(unit * k), fonts)
        gap = 0.08 * unit * k                  # letter-spacing
        widths = [font.getlength(ch) for ch in text]
        total = sum(widths) + gap * (len(text) - 1)
        if total / r <= math.radians(2 * LABEL_MAX_DEG) or unit <= 10:
            break
        unit -= 1
    half = round(unit * k * 1.2)
    pos = -total / 2
    for ch, w in zip(text, widths):
        phi = -math.pi / 2 + (pos + w / 2) / r
        glyph = Image.new("L", (2 * half, 2 * half), 0)
        ImageDraw.Draw(glyph).text((half, half), ch, font=font, fill=round(255 * alpha), anchor="ms")
        glyph = glyph.rotate(-math.degrees(phi + math.pi / 2), resample=Image.BICUBIC)
        x = round(C * k + r * math.cos(phi)) - half
        y = round(C * k + r * math.sin(phi)) - half
        im.paste(color, (x, y, x + 2 * half, y + 2 * half), glyph)
        pos += w + gap
