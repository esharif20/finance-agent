"""The `fa` banner: a gold coin with a pound on its face spins in from the right
across a gradient wordmark, wiping it into view, and settles face-on.

Everything is drawn on a small pixel canvas and printed with half-blocks
(two pixels per cell, so the pixels are square). Truecolor when the terminal
says so, else the 256-colour cube; plain blocks under NO_COLOR. It animates
only on a wide TTY outside CI with animation allowed, and prints nothing at all
when stdout is not a TTY, so piped commands are never delayed or polluted."""

import math
import os
import shutil
import sys
import time
from collections.abc import Callable, Mapping
from typing import TextIO

RGB = tuple[int, int, int]
Canvas = list[list[RGB | None]]

R = 6  # coin radius in pixels: 12 px = 6 rows
COIN_W = 2 * R
GAP = 3
FRAMES = 30
FRAME_S = 0.04  # 30 frames x 40 ms = 1.2 s
MIN_COLS = 60

GOLD, GOLD_LIT, GOLD_RIM, GOLD_DEEP = (
    (242, 190, 72),
    (255, 228, 140),
    (205, 142, 38),
    (122, 74, 10),
)
EDGE = ((150, 98, 22), (110, 70, 14))  # milled edge, alternate rows
SHADOW = (52, 47, 40)
# gold to amber to emerald: warm, nowhere near the 235-290 hue band
STOPS = ((247, 215, 116), (242, 140, 60), (47, 181, 140))
TAGLINE = "your money, on your Mac. local-first, read-only."

POUND = (  # 5 x 7
    "..##.",
    ".#..#",
    ".#...",
    "####.",
    ".#...",
    ".#...",
    "#####",
)
FONT = {  # 4 x 5 (I and T 3 wide)
    "F": ("####", "#...", "###.", "#...", "#..."),
    "I": ("###", ".#.", ".#.", ".#.", "###"),
    "N": ("#..#", "##.#", "#.##", "#..#", "#..#"),
    "A": (".##.", "#..#", "####", "#..#", "#..#"),
    "C": (".###", "#...", "#...", "#...", ".###"),
    "E": ("####", "#...", "###.", "#...", "####"),
    "G": (".###", "#...", "#.##", "#..#", ".###"),
    "T": ("###", ".#.", ".#.", ".#.", ".#."),
}


def _word(text: str) -> list[str]:
    rows = [""] * 5
    for i, ch in enumerate(text):
        for r in range(5):
            rows[r] += ("." if i else "") + FONT[ch][r]
    return rows


LINES = (_word("FINANCE"), _word("AGENT"))
WORD_X = COIN_W + GAP
WIDTH = WORD_X + len(LINES[0][0])
HEIGHT = 2 * R + 2  # coin, then a shadow row pair


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def _gradient(t: float) -> RGB:
    t = min(max(t, 0.0), 1.0) * (len(STOPS) - 1)
    i = min(int(t), len(STOPS) - 2)
    return _mix(STOPS[i], STOPS[i + 1], t - i)


def _blank() -> Canvas:
    return [[None] * WIDTH for _ in range(HEIGHT)]


def _wordmark(c: Canvas, reveal_from: float) -> None:
    """Draw the wordmark right of `reveal_from`: the coin wipes it into view."""
    span = len(LINES[0][0]) - 1
    for li, rows in enumerate(LINES):
        y0 = li * 6 + 1
        for r, row in enumerate(rows):
            for x, bit in enumerate(row):
                if bit == "#" and WORD_X + x >= reveal_from:
                    c[y0 + r][WORD_X + x] = _gradient(x / span)


def _coin(c: Canvas, cx: float, theta: float) -> None:
    """A coin spun by `theta` about its vertical axis, centred at cx."""
    cos, sin = math.cos(theta), math.sin(theta)
    a = max(R * abs(cos), 0.55)  # projected half-width
    shift = 1.6 * sin  # the milled edge shows on one side as it turns
    cy = R
    for y in range(2 * R):
        dy = y + 0.5 - cy
        for x in range(
            max(0, math.floor(cx - R - 2)), min(WIDTH, math.ceil(cx + R + 2))
        ):
            dx = x + 0.5 - cx
            face = (dx / a) ** 2 + (dy / R) ** 2
            if face <= 1:
                c[y][x] = _face_px(dx * R / a, dy, face, front=cos >= 0)
            elif ((dx + shift) / a) ** 2 + (dy / R) ** 2 <= 1:
                c[y][x] = EDGE[y % 2]
    w = max(round(0.8 * (a + abs(shift))), 1)  # a soft shadow, a pixel below
    for x in range(max(0, round(cx) - w), min(WIDTH, round(cx) + w)):
        c[2 * R + 1][x] = SHADOW


def _face_px(u: float, v: float, face: float, front: bool) -> RGB:
    """Colour at face-space (u, v); u is unsquashed to the full radius."""
    if not front:
        u = -u
    if face > 0.7:  # the rim, lit from the top left
        return GOLD_LIT if u + v < -R * 0.6 else GOLD_RIM
    col, row = math.floor(u + 2.5), math.floor(v + 3.5)
    if front and 0 <= row < 7 and 0 <= col < 5 and POUND[row][col] == "#":
        return GOLD_DEEP
    if not front and 0.32 < face < 0.45:  # the back: an inner ring
        return GOLD_RIM
    return _mix(GOLD_LIT, GOLD, min(max((u + v) / (2 * R) + 0.5, 0.0), 1.0))


def frame(i: int, n: int = FRAMES) -> Canvas:
    """Frame i of n: the coin eases in from the right, spinning 1.5 turns, and
    lands face-on at the left of the wordmark."""
    t = i / (n - 1)
    ease = 1 - (1 - t) ** 3
    start, end = WIDTH + R, float(R)
    cx = start + (end - start) * ease
    c = _blank()
    _wordmark(c, cx + R)
    _coin(c, cx, (1 - ease) * 3 * math.pi)
    return c


def final() -> Canvas:
    return frame(FRAMES - 1)


# --- terminal ---------------------------------------------------------------


def _cube(rgb: RGB) -> int:
    def q(v: int) -> int:
        return 0 if v < 48 else 1 if v < 115 else (v - 35) // 40

    return 16 + 36 * q(rgb[0]) + 6 * q(rgb[1]) + q(rgb[2])


def _sgr(rgb: RGB, bg: bool, truecolor: bool) -> str:
    base = 48 if bg else 38
    if truecolor:
        return f"{base};2;{rgb[0]};{rgb[1]};{rgb[2]}"
    return f"{base};5;{_cube(rgb)}"


def render(c: Canvas, colour: bool = True, truecolor: bool = True) -> list[str]:
    """Half-block lines: the top pixel is the foreground of '▀', the bottom
    pixel its background. Without colour, filled pixels print as blocks."""
    out: list[str] = []
    for y in range(0, len(c), 2):
        top, bot = c[y], c[y + 1] if y + 1 < len(c) else [None] * len(c[y])
        line = ""
        for t, b in zip(top, bot, strict=True):
            if not colour:
                line += " ▀▄█"[(t is not None) + 2 * (b is not None)]
            elif t is None and b is None:
                line += " "
            elif t is None and b is not None:
                line += f"\x1b[{_sgr(b, False, truecolor)}m▄\x1b[0m"
            elif b is None and t is not None:
                line += f"\x1b[{_sgr(t, False, truecolor)}m▀\x1b[0m"
            elif t is not None and b is not None:
                fg, bgc = _sgr(t, False, truecolor), _sgr(b, True, truecolor)
                line += f"\x1b[{fg};{bgc}m▀\x1b[0m"
        out.append(line.rstrip())
    return out


def mode(stream: TextIO, env: Mapping[str, str], cols: int) -> str:
    """'off' (no TTY), 'static' or 'animate'."""
    if not stream.isatty():
        return "off"
    if env.get("NO_COLOR") or env.get("FA_NO_ANIMATION") or env.get("CI"):
        return "static"
    return "animate" if cols >= MIN_COLS else "static"


def show(
    status: str | None = None,
    stream: TextIO | None = None,
    env: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    out: TextIO = stream or sys.stdout
    env = os.environ if env is None else env
    m = mode(out, env, shutil.get_terminal_size((80, 24)).columns)
    if m == "off":
        return
    colour = not env.get("NO_COLOR")
    truecolor = env.get("COLORTERM", "") in ("truecolor", "24bit")
    tagline = f"\x1b[2m{TAGLINE}\x1b[0m" if colour else TAGLINE
    if m == "animate":
        out.write("\x1b[?25l")  # hide the cursor while drawing
        try:
            for i in range(FRAMES - 1):
                lines = render(frame(i), colour, truecolor)
                out.write("\n".join(f"\x1b[2K{ln}" for ln in lines) + "\n")
                out.write(f"\x1b[{len(lines)}F")
                out.flush()
                sleep(FRAME_S)
        finally:
            out.write("\x1b[?25h")
    for ln in render(final(), colour, truecolor):
        out.write(f"\x1b[2K{ln}\n" if m == "animate" else f"{ln}\n")
    out.write(tagline + "\n")
    if status:
        out.write(status + "\n")
    out.flush()


# --- README recording ---------------------------------------------------------


def svg(status: str, px: int = 8, frames: int = 16) -> str:
    """An animated SVG of the banner for the README (SMIL frames, then the
    final frame frozen), with a status line that must be synthetic."""
    chars = max(len(status), len(TAGLINE))  # monospace: about 0.62 em a glyph
    w = max(WIDTH + 2, round(chars * 1.4 * 0.62) + 3) * px
    h = HEIGHT * px + 5 * px
    hexc = "#{:02x}{:02x}{:02x}".format
    dt = 1.2 / frames

    def rects(c: Canvas) -> str:
        parts: list[str] = []
        for y, row in enumerate(c):
            x = 0
            while x < len(row):
                col = row[x]
                if col is None:
                    x += 1
                    continue
                run = x
                while run < len(row) and row[run] == col:
                    run += 1
                parts.append(
                    f'<rect x="{(x + 1) * px}" y="{(y + 1) * px}" '
                    f'width="{(run - x) * px}" height="{px}" fill="{hexc(*col)}"/>'
                )
                x = run
        return "".join(parts)

    groups: list[str] = []
    for i in range(frames):
        c = frame(i, frames)
        last = i == frames - 1
        anim = (
            f'<set attributeName="opacity" to="1" begin="{i * dt:.3f}s" fill="freeze"/>'
            if last
            else f'<set attributeName="opacity" to="1" begin="{i * dt:.3f}s" '
            f'dur="{dt:.3f}s"/>'
        )
        groups.append(f'<g opacity="0">{anim}{rects(c)}</g>')
    ty = (HEIGHT + 2) * px
    font = 'font-family="ui-monospace,SFMono-Regular,Menlo,monospace"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="finance-agent banner" '
        'shape-rendering="crispEdges">'
        f'<rect width="{w}" height="{h}" rx="{px * 1.5}" fill="#141210"/>'
        + "".join(groups)
        + f'<text x="{px}" y="{ty}" {font} font-size="{px * 1.4}" '
        f'fill="#9a9184">{TAGLINE}</text>'
        f'<text x="{px}" y="{ty + 2 * px}" {font} font-size="{px * 1.4}" '
        f'fill="#e8e1d6">{status}</text></svg>\n'
    )
