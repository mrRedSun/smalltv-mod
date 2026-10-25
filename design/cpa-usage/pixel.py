"""Pixel-art mockup of the SmallTV CLIProxyAPI usage screen.

Draws on a 120x120 art grid (each art pixel = 2x2 panel pixels on the 240x240
ST7789), quantises colours to RGB565, and writes PNGs. Everything here maps
1:1 onto firmware primitives: fillRect per art pixel, the classic 5x7 GFX font,
and Bayer-dithered gradients.
"""
import math, re, struct, zlib
from pathlib import Path

HERE = Path(__file__).parent
G = 120  # art grid

# ---------- classic Adafruit GFX 5x7 font ----------
_src = (HERE / "glcdfont.c").read_text()
_bytes = [int(b, 16) for b in re.findall(r"0x([0-9A-Fa-f]{2})", _src.split("{", 1)[1])]
FONT = {chr(i): _bytes[i * 5:i * 5 + 5] for i in range(len(_bytes) // 5)}
def _glyph(rows):
    return [sum(1 << r for r in range(len(rows)) if rows[r][c] == "#") for c in range(5)]

# custom glyph: '~' = hourglass used before reset countdowns
FONT["~"] = _glyph(["#####", "#...#", ".#.#.", "..#..", ".###.", "#####", "....."])
# custom glyph: centred dot separating provider and account label
FONT["\xfa"] = _glyph([".....", ".....", ".##..", ".##..", ".....", ".....", "....."])

def hexrgb(h):
    n = int(h.lstrip("#"), 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)

def q565(c):
    r, g, b = c
    r, g, b = r >> 3, g >> 2, b >> 3
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))

def mix(a, b, t):
    a, b = hexrgb(a) if isinstance(a, str) else a, hexrgb(b) if isinstance(b, str) else b
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]

class Art:
    def __init__(self):
        self.px = [[(0, 0, 0)] * G for _ in range(G)]

    def set(self, x, y, c):
        if 0 <= x < G and 0 <= y < G:
            self.px[y][x] = hexrgb(c) if isinstance(c, str) else c

    def get(self, x, y):
        return self.px[y][x]

    def rect(self, x, y, w, h, c):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.set(xx, yy, c)

    # Dithered gradient through a list of stops; `axis` 'x' or 'y'.
    def grad(self, x, y, w, h, stops, axis="y", mask=None):
        n = len(stops) - 1
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if mask and not mask(xx, yy):
                    continue
                t = ((yy - y) / max(1, h - 1)) if axis == "y" else ((xx - x) / max(1, w - 1))
                f = t * n
                i = min(int(f), n - 1)
                frac = f - i
                c = stops[i + 1] if frac * 16 > BAYER[yy % 4][xx % 4] + 0.5 else stops[i]
                self.set(xx, yy, c)

    def glyphs(self, s, x, y, scale=1):
        """Yield lit art-pixel coords for string s (5x7 + 1px spacing)."""
        for ch in s:
            cols = FONT.get(ch, FONT["?"])
            for cx, col in enumerate(cols):
                for cy in range(8):
                    if col >> cy & 1:
                        for sy in range(scale):
                            for sx in range(scale):
                                yield x + cx * scale + sx, y + cy * scale + sy
            x += 6 * scale

    @staticmethod
    def tw(s, scale=1):
        return len(s) * 6 * scale - scale

    def text(self, s, x, y, c, scale=1, glow=None, align="left", grad=None):
        if align == "right":
            x -= self.tw(s, scale)
        elif align == "center":
            x -= self.tw(s, scale) // 2
        pts = list(self.glyphs(s, x, y, scale))
        if glow:
            lit = set(pts)
            for (px, py) in pts:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if (px + dx, py + dy) not in lit:
                            self.set(px + dx, py + dy, glow)
        if grad:  # vertical gradient through the glyph height
            h = 7 * scale
            for (px, py) in pts:
                t = (py - y) / max(1, h - 1)
                self.set(px, py, mix(grad[0], grad[1], min(1, t)))
        else:
            for p in pts:
                self.set(*p, c)

    def sprite(self, rows, pal, x, y, glow=None):
        lit = {(x + cx, y + cy) for cy, r in enumerate(rows) for cx, ch in enumerate(r) if ch != "."}
        if glow:
            for (px, py) in lit:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if (px + dx, py + dy) not in lit:
                            self.set(px + dx, py + dy, glow)
        for cy, r in enumerate(rows):
            for cx, ch in enumerate(r):
                if ch != ".":
                    c = pal[ch]
                    self.set(x + cx, y + cy, c(cy) if callable(c) else c)

    def png(self, path, scale=2):
        W = G * scale
        raw = bytearray()
        for y in range(W):
            raw.append(0)
            for x in range(W):
                raw.extend(q565(self.px[y // scale][x // scale]))
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, W, 8, 2, 0, 0, 0)) \
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
        Path(path).write_bytes(data)

# ---------- palette ----------
BG_TOP, BG_MID, BG_BOT = "#1c0638", "#10032a", "#06020f"
INK, INK2, INK3 = "#f4efff", "#a99ad6", "#7d6db0"
TRACK = "#24123f"
PROVIDERS = {
    "claude": {"name": "CLAUDE", "a": "#ffb04a", "b": "#ff3d8b", "glow": "#4a1430", "dim": "#3a1226"},
    "codex":  {"name": "CODEX",  "a": "#3df2ff", "b": "#8f5bff", "glow": "#123a55", "dim": "#14203f"},
}

# ---------- icons ----------
CLAWD = [  # Claude Code's Clawd, from the firmware's mascot frames
    "..###########..",
    "..###########..",
    "..##o#####o##..",
    "####o#####o####",
    "###############",
    "###############",
    "#.###########.#",
    "..###########..",
    "..###########..",
    "..###########..",
    "..#..#...#..#..",
    "..#..#...#..#..",
]
TERM = [  # Codex: neon terminal window with a >_ prompt
    ".#############.",
    "#hhhhhhhhhhhhh#",
    "#hohohhhhhhhhh#",
    "###############",
    "#.............#",
    "#.p...........#",
    "#..p..........#",
    "#...p.........#",
    "#..p..........#",
    "#.p...uuuu....#",
    "#.............#",
    ".#############.",
]

def icon(art, key, x, y, frame=0):
    p = PROVIDERS[key]
    if key == "claude":
        rows = CLAWD
        if frame % 32 in (22, 23):                       # blink
            rows = [r.replace("o", "#") for r in rows]
        pal = {"#": lambda r: mix("#ff9a5a", "#e2552e", r / 11), "o": "#1a0a12"}
        art.sprite(rows, pal, x, y, glow=p["glow"])
    else:
        rows = TERM if (frame // 8) % 2 == 0 else [r.replace("u", ".") for r in TERM]   # cursor blink
        pal = {"#": lambda r: mix(p["a"], p["b"], r / 11), "h": "#1d3f66", "o": "#3df2ff",
               "p": "#f4efff", "u": "#f4efff"}
        art.sprite(rows, pal, x, y, glow=p["glow"])

def seg_bar(art, x, y, w, h, frac, key, segs=8):
    """Segmented neon bar: gradient-lit segments, dim empty ones, glow halo.
    Segment edges are rounded so the last segment ends exactly at x+w-1."""
    p = PROVIDERS[key]
    gap = 1
    edges = [(x + round(i * (w + gap) / segs), x + round((i + 1) * (w + gap) / segs) - gap) for i in range(segs)]
    frac = max(0.0, min(1.0, frac))
    lit_n = max(1, round(frac * segs)) if frac > 0 else 0   # any usage lights a segment
    if lit_n:
        art.rect(x - 1, y, edges[lit_n - 1][1] - x + 2, h, p["glow"])   # side glow only; rows stay clear
    for i, (sx, ex) in enumerate(edges):
        if i < lit_n:
            for xx in range(sx, ex):
                art.rect(xx, y, 1, h, mix(p["a"], p["b"], (xx - x) / max(1, w - 1)))
            art.rect(sx, y, ex - sx, 1, mix(INK, mix(p["a"], p["b"], (sx - x) / w), 0.5))  # top highlight
        else:
            art.rect(sx, y, ex - sx, h, p["dim"])

def background(art):
    art.grad(0, 0, G, G, [BG_TOP, BG_MID, BG_BOT, BG_BOT], axis="y")
    # faint scanlines every other art row
    for y in range(1, G, 2):
        for x in range(G):
            art.set(x, y, mix(art.get(x, y), (0, 0, 0), 0.18))

# ---------- sample data ----------
DATA = {"clock": "14:32", "day": "SAT", "date": "25 OCT"}

def header(art, y=2, frame=0):
    """Neon clock: 3-stop gradient digits over a 1px outline glow, with a retro colour
    split (cyan left, pink lower-right). The colon fades out and back in once a second."""
    x = 4
    hh, mm = DATA["clock"].split(":")
    def digits(s, x):
        for dx, dy, col in ((-1, 0, "#1b6f96"), (1, 1, "#8a1f6a")):         # colour split
            for (px, py) in art.glyphs(s, x + dx, y + dy, 2):
                art.set(px, py, col)
        art.text(s, x, y, INK, scale=2, glow="#2a0c4a")
        for (px, py) in art.glyphs(s, x, y, 2):                            # 3-stop gradient
            t_ = (py - y) / 13
            art.set(px, py, mix("#ffffff", "#e3c8ff", t_ * 2) if t_ < 0.5 else mix("#e3c8ff", "#ff7ac8", t_ * 2 - 1))
    digits(hh, x)
    # Colon: fades with a 16-frame (~1.1 s) period; never fully gone so it reads as a pulse.
    k = (1 + math.cos(2 * math.pi * frame / 16)) / 2
    cx = x + 25
    for oy in (3, 9):
        art.rect(cx, y + oy, 2, 2, mix("#2a1650", "#3df2ff", k))
        if k > 0.5:
            for gx, gy in ((-1, 0), (2, 0), (0, -1), (0, 2), (1, -1), (1, 2), (-1, 1), (2, 1)):
                art.set(cx + gx, y + oy + gy, mix(art.get(cx + gx, y + oy + gy), "#123a55", (k - 0.5) * 2))
    digits(mm, x + 30)

    # Date tag: day in a neon pill, date under it.
    day, date = DATA["day"], DATA["date"]
    R = 115
    pw = art.tw(day) + 6
    px0 = R - pw + 1
    art.grad(px0, y - 1, pw, 9, ["#ff3d8b", "#8f5bff"], axis="x")
    for cx_, cy_ in ((px0, y - 1), (R, y - 1), (px0, y + 7), (R, y + 7)):   # chamfer
        art.set(cx_, cy_, BG_TOP)
    art.text(day, R - 2, y, "#14041f", align="right")
    art.text(date, R, y + 9, INK, align="right", glow="#2a0c4a", grad=("#ffffff", "#a99ad6"))

    # Divider: cyan->violet->pink, with a bright sweep that runs once per loop.
    sweep = 4 + round(112 * frame / FRAMES)
    for xx in range(4, 116):
        c = mix("#3df2ff", "#8f5bff", (xx - 4) / 56) if xx < 60 else mix("#8f5bff", "#ff3d8b", (xx - 60) / 55)
        d = abs(xx - sweep)
        if d < 10:
            c = mix(c, "#ffffff", (1 - d / 10) ** 2)
        art.set(xx, y + 17, c)
        art.set(xx, y + 18, mix(c, BG_BOT, 0.7))

# ---------- provider cards (animated) ----------
FRAMES = 32          # one loop; ~70 ms per frame on the device -> 2.2 s

def perimeter(x, y, w, h):
    """Clockwise frame pixels (chamfered corners) with a 0..1 position for the base gradient."""
    pts = [(xx, y, "top") for xx in range(x + 1, x + w - 1)]
    pts += [(x + w - 1, yy, "right") for yy in range(y + 1, y + h - 1)]
    pts += [(xx, y + h - 1, "bottom") for xx in range(x + w - 2, x, -1)]
    pts += [(x, yy, "left") for yy in range(y + h - 2, y, -1)]
    return pts

def neon_frame(art, x, y, w, h, p, gap, glowk, comet, hot):
    """Gradient frame. `glowk` 0..1 brightens the whole frame (synced to the icon bob);
    `comet` is the head index of a light running clockwise; hot cards pulse red."""
    art.grad(x + 1, y + 1, w - 2, h - 2, ["#1e0a3c", "#120628"], axis="y")
    pts = perimeter(x, y, w, h)
    hi = "#ff4d6d" if hot else "#ffffff"
    tail = 16
    for i, (px, py, side) in enumerate(pts):
        if side == "top" and gap[0] <= px <= gap[1]:
            continue
        if side == "top":      c = mix(p["a"], p["b"], (px - x) / (w - 1))
        elif side == "right":  c = mix(p["b"], p["dim"], (py - y) / (h - 1))
        elif side == "bottom": c = mix(p["glow"], p["dim"], (px - x) / (w - 1))
        else:                  c = mix(p["a"], p["glow"], (py - y) / (h - 1))
        c = mix(c, hi, 0.45 * glowk)
        d = (comet - i) % len(pts)
        if d < tail:
            c = mix(c, "#ffffff", (1 - d / tail) ** 1.5)
        art.set(px, py, c)

def card(art, acct, y0, frame, phase, H=45):
    key = acct["provider"]
    p = PROVIDERS[key]
    name = p["name"] + ("\xfa" + acct["label"] if acct.get("label") else "")
    hot = max(acct["5H"][0], acct["WEEK"][0]) >= 0.9
    # Icon bob: 2 art px each way. The frame brightens as the icon rises; hot cards
    # pulse twice as fast in red.
    a = 2 * math.pi * frame / FRAMES + phase
    bob = round(2 * math.sin(a))
    glowk = (1 - math.sin(a * (2 if hot else 1))) / 2
    lx = 10
    plen = len(perimeter(4, y0, 112, H))
    comet = round((frame / FRAMES) * plen + phase / (2 * math.pi) * plen) % plen
    neon_frame(art, 4, y0, 112, H, p, (lx - 2, lx + art.tw(name) + 1), glowk, comet, hot)
    art.text(name, lx, y0 - 3, INK, glow=mix(p["glow"], p["a"], 0.5 * glowk),
             grad=("#ffffff", mix(p["a"], INK, 0.3)))
    iy = y0 + (H - 12) // 2 + bob
    # Ground shadow: wider when the icon is low.
    sw = 9 + (bob + 2)
    art.rect(15 - sw // 2, y0 + (H - 12) // 2 + 15, sw, 1, "#0a0418")
    icon(art, key, 8, iy, frame)
    X, R = 27, 111                      # content column: left edge, right edge (inclusive)
    for j, win in enumerate(["5H", "WEEK"]):
        f, reset = acct[win]
        r = y0 + 6 + j * 19
        h = f >= 0.9
        art.text(win, X, r, mix(p["a"], INK, 0.45))
        art.text("~", X, r + 8, p["a"])
        art.text(reset, X + 6, r + 8, INK2)
        art.text(f"{round(f * 100)}%", R + 1, r, INK, scale=2, align="right",
                 glow="#5a0f22" if h else p["glow"],
                 grad=("#ffd0d8", "#ff4d6d") if h else ("#ffffff", mix(p["a"], INK, 0.35)))
        seg_bar(art, X, r + 16, R - X + 1, 2, f, key, segs=12)

def pick_two(accounts):
    """Top two accounts by 5-hour usage (weekly breaks ties); unused ones only fill in."""
    ranked = sorted(accounts, key=lambda a: (a["5H"][0], a["WEEK"][0]), reverse=True)[:2]
    if ranked[0]["provider"] != ranked[1]["provider"]:
        ranked = [dict(a, label=None) for a in ranked]   # provider name is enough
    return ranked

def cards(accounts, frame=0):
    art = Art(); background(art); header(art, y=2, frame=frame)
    top = pick_two(accounts)
    card(art, top[0], 25, frame, 0.0)
    card(art, top[1], 74, frame, math.pi)       # opposite phase: one rises as the other sinks
    return art

MIXED = [
    {"provider": "claude", "label": "HOME", "5H": (0.62, "2H14M"), "WEEK": (0.38, "3D04H")},
    {"provider": "codex",  "label": "",     "5H": (0.91, "0H41M"), "WEEK": (0.57, "5D11H")},
]
TWO_CLAUDES = [
    {"provider": "codex",  "label": "",     "5H": (0.0, "5H00M"),  "WEEK": (0.0, "7D00H")},
    {"provider": "claude", "label": "WORK", "5H": (0.74, "1H02M"), "WEEK": (0.66, "2D19H")},
    {"provider": "claude", "label": "HOME", "5H": (0.18, "3H47M"), "WEEK": (0.41, "4D08H")},
]
STRESS = [
    {"provider": "claude", "label": "", "5H": (1.0, "NOW"),    "WEEK": (0.0, "6D23H")},
    {"provider": "codex",  "label": "", "5H": (0.04, "4H59M"), "WEEK": (0.995, "12M")},
]

if __name__ == "__main__":
    import shutil, subprocess
    frames, shots = HERE / "frames", HERE / "screenshots"
    frames.mkdir(exist_ok=True); shots.mkdir(exist_ok=True)
    for name, accts in [("mixed", MIXED), ("claudes", TWO_CLAUDES)]:
        for f in range(FRAMES):
            cards(accts, f).png(frames / f"{name}_{f:02d}.png")
        cards(accts, 2).png(shots / f"{name}.png", scale=4)
        # Animated GIF at 2x panel size (one loop = 32 frames at 70 ms, as on the device).
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", "100/7",
                            "-i", str(frames / f"{name}_%02d.png"), "-vf",
                            "scale=480:480:flags=neighbor,split[a][b];[a]palettegen=max_colors=256[p];[b][p]paletteuse=dither=none",
                            "-loop", "0", str(shots / f"{name}.gif")], check=True)
    cards(STRESS, 0).png(shots / "stress.png", scale=4)
    print("ok")
