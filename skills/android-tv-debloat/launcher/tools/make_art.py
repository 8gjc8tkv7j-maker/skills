#!/usr/bin/env python3
"""Generate the built-in wallpapers, icon and banner for TV Home.

The launcher downloads real nature photos from Wikimedia Commons when the TV is online; these
procedural landscapes are the offline fallback, so the app ships with no third-party images.

Usage: python3 make_art.py [launcher_dir]      (needs numpy and Pillow)
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080


# ------------------------------------------------------------------ noise helpers

def fbm1d(n, seed, octaves=6, base=4, rough=0.5):
    """Fractal 1-D value noise in roughly [-1, 1]."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, n)
    out = np.zeros(n)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        k = base * 2 ** o
        pts = rng.uniform(-1, 1, k + 2)
        t = x * k
        i = np.floor(t).astype(int)
        f = t - i
        f = f * f * (3 - 2 * f)
        out += amp * (pts[i] * (1 - f) + pts[i + 1] * f)
        total += amp
        amp *= rough
    return out / total


def fbm2d(h, w, seed, octaves=5, base=3, rough=0.55, stretch=(1, 1)):
    """Fractal 2-D value noise in [0, 1], built by upscaling random grids."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        gw = max(2, int(base * 2 ** o * stretch[0]))
        gh = max(2, int(base * 2 ** o * stretch[1] * h / w))
        grid = Image.fromarray((rng.uniform(0, 255, (gh, gw))).astype(np.uint8))
        out += amp * np.asarray(grid.resize((w, h), Image.BICUBIC), dtype=float) / 255
        total += amp
        amp *= rough
    return out / total


def vgrad(stops, h=H):
    """Vertical gradient from (position 0..1, rgb) stops -> (h, 1, 3) array."""
    y = np.linspace(0, 1, h)
    cols = []
    for ch in range(3):
        cols.append(np.interp(y, [s[0] for s in stops], [s[1][ch] for s in stops]))
    return np.stack(cols, -1)[:, None, :] / 255.0


def coverage(ridge):
    """Anti-aliased mask of everything below a ridge line y(x)."""
    yy = np.arange(H)[:, None]
    return np.clip(yy - ridge[None, :] + 0.5, 0, 1)[..., None]


def lerp(a, b, t):
    return a + (b - a) * t


def glow(cx, cy, radius, power=2.0):
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
    return np.clip(1 - d, 0, 1) ** power


def rgb(hexstr):
    return np.array([int(hexstr[i:i + 2], 16) for i in (0, 2, 4)]) / 255.0


def save(img, path):
    arr = (np.clip(np.nan_to_num(img), 0, 1) * 255).astype(np.uint8)
    im = Image.fromarray(arr)
    # A whisper of grain hides banding on 8-bit TV panels.
    noise = np.random.default_rng(7).normal(0, 1.6, arr.shape)
    im = Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))
    im.save(path, quality=88, optimize=True, progressive=True)
    return im


# ------------------------------------------------------------------ scenes

def alpine_dusk():
    sky = vgrad([(0, (28, 37, 82)), (0.35, (92, 82, 140)), (0.55, (232, 150, 140)),
                 (0.66, (255, 200, 150)), (1, (255, 214, 170))])
    img = np.broadcast_to(sky, (H, W, 3)).copy()
    img += glow(W * 0.62, H * 0.6, 700, 2.2)[..., None] * rgb("ffcf9a") * 0.55
    layers = [
        (0.50, 150, "b48aa8", 0.70, 11),
        (0.56, 170, "8a6a95", 0.55, 12),
        (0.63, 160, "5d4a78", 0.40, 13),
        (0.72, 130, "3a3058", 0.22, 14),
        (0.83, 110, "1d1a33", 0.08, 15),
    ]
    haze = rgb("f2b8a8")
    for base, amp, col, fog, seed in layers:
        n = fbm1d(W, seed, octaves=8, base=3, rough=0.55)
        ridge = H * base - amp * np.clip(1 - np.abs(n * 1.6), 0, 1) ** 1.5 + amp * 0.3
        c = lerp(rgb(col), haze, fog)
        shade = vgrad([(0, (255, 255, 255)), (1, (150, 150, 160))])
        layer = c * (0.85 + 0.15 * shade)
        m = coverage(ridge)
        img = img * (1 - m) + layer * m
        # Mist pooling at the foot of each range.
        yy = np.arange(H)[:, None]
        mist = np.clip(1 - (yy - (ridge[None, :] + 40)) / 140, 0, 1) * np.clip((yy - ridge[None, :]) / 40, 0, 1)
        img += (mist[..., None] * 0.12 * fog) * haze
    return img


def aurora_lake():
    horizon = int(H * 0.66)
    sky = vgrad([(0, (4, 8, 24)), (0.45, (8, 26, 48)), (0.66, (18, 60, 72)), (1, (18, 60, 72))])
    img = np.broadcast_to(sky, (H, W, 3)).copy()
    rng = np.random.default_rng(21)
    stars = np.zeros((H, W))
    k = 1400
    sx, sy = rng.integers(0, W, k), rng.integers(0, horizon, k)
    stars[sy, sx] = rng.uniform(0.3, 1.0, k) ** 3
    stars = np.asarray(Image.fromarray((stars * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8)),
                       dtype=float) / 255 * 3
    img += stars[..., None] * 0.9
    # Aurora: a wavy curtain with vertical rays and a long upward fade.
    yy = np.arange(H)[:, None].astype(float)
    centre = H * 0.42 + 70 * fbm1d(W, 22, octaves=4, base=2) + 40 * np.sin(np.linspace(0, 3.2, W))
    rays = 0.55 + 0.45 * fbm1d(W, 23, octaves=6, base=40, rough=0.6)
    d = yy - centre[None, :]
    below = np.exp(-(np.clip(d, 0, None) / 22) ** 2)
    above = np.exp(-np.clip(-d, 0, None) / 210)
    band = np.where(d > 0, below, above) * rays[None, :]
    band *= np.clip(1.2 - np.abs(np.linspace(-1, 1, W)) * 0.5, 0, 1)[None, :]
    tint = np.clip(-d / 380, 0, 1)[..., None]
    colour = lerp(rgb("3dffa8"), rgb("8a5cff"), tint * 0.8)
    img += band[..., None] * colour * 0.85
    img += glow(W * 0.5, centre.mean(), 900, 2)[..., None] * rgb("1fae8a") * 0.15
    # Mountains.
    n = fbm1d(W, 24, octaves=8, base=3)
    ridge = horizon - 120 * np.clip(1 - np.abs(n * 1.5), 0, 1) ** 1.6 - 10
    m = coverage(ridge)
    img = img * (1 - m) + rgb("05080d") * m
    # Lake: mirror with ripples, darker and bluer.
    depth = np.arange(H - horizon)
    src = np.clip(horizon - 1 - depth, 0, H - 1)
    ripple = (fbm2d(H - horizon, W, 25, octaves=4, base=6, stretch=(1, 6)) - 0.5) * 14
    xs = np.clip((np.arange(W)[None, :] + ripple).astype(int), 0, W - 1)
    lake = img[src[:, None], xs] * 0.55 + rgb("02060c") * 0.25
    img[horizon:] = lake
    shore = np.exp(-((np.arange(H) - horizon) / 3.0) ** 2)[:, None, None]
    img = img * (1 - shore * 0.8)
    return img


def misty_forest():
    sky = vgrad([(0, (176, 196, 204)), (0.5, (222, 226, 220)), (1, (240, 236, 224))])
    img = np.broadcast_to(sky, (H, W, 3)).copy()
    img += glow(W * 0.78, H * 0.18, 900, 1.6)[..., None] * rgb("fff4dc") * 0.35
    fogc = rgb("e8ebe6")
    rng = np.random.default_rng(31)
    layers = [(0.50, 70, 0.80), (0.58, 95, 0.62), (0.67, 130, 0.42), (0.78, 180, 0.22), (0.92, 260, 0.05)]
    for li, (base, tree_h, fog) in enumerate(layers):
        S = 2
        mask = Image.new("L", (W * S, H * S), 0)
        dr = ImageDraw.Draw(mask)
        ground = H * base + 18 * fbm1d(W, 40 + li, octaves=4, base=2)
        dr.polygon([(0, H * S)] + [(x * S, ground[x] * S) for x in range(0, W, 8)] + [(W * S, H * S)], fill=255)
        x = -30.0
        while x < W + 30:
            h = tree_h * rng.uniform(0.65, 1.25)
            w = h * rng.uniform(0.26, 0.34)
            gx = int(np.clip(x, 0, W - 1))
            top = ground[gx] - h
            tiers = rng.integers(4, 7)
            pts = [(x * S, top * S)]
            for t in range(1, tiers + 1):
                y = top + h * t / tiers
                half = w / 2 * (0.35 + 0.65 * t / tiers)
                pts.append(((x + half) * S, y * S))
                if t < tiers:
                    pts.append(((x + half * 0.45) * S, (y - h / tiers * 0.15) * S))
            right = pts
            left = [(2 * x * S - px, py) for px, py in reversed(right[1:])]
            dr.polygon(right + left, fill=255)
            dr.rectangle([(x - w * 0.04) * S, (ground[gx] - h * 0.08) * S, (x + w * 0.04) * S, ground[gx] * S], fill=255)
            x += w * rng.uniform(0.45, 0.9)
        m = np.asarray(mask.resize((W, H), Image.LANCZOS), dtype=float)[..., None] / 255
        col = lerp(rgb("1f3326"), fogc, fog)
        img = img * (1 - m) + col * m
        # Fog band rising from behind the next row of trees.
        yy = np.arange(H)[:, None]
        band = np.exp(-((yy - (ground[None, :] + 30)) / 60) ** 2) * (0.35 + 0.35 * fbm2d(H, W, 50 + li, base=3))
        img = img * (1 - band[..., None] * 0.5) + fogc * band[..., None] * 0.5
    # Soft light rays from the upper right.
    yy, xx = np.mgrid[0:H, 0:W]
    ang = np.arctan2(yy - H * 0.05, xx - W * 0.85)
    rays = (0.5 + 0.5 * np.sin(ang * 38 + fbm2d(H, W, 60, base=2) * 3)) ** 6
    img += (rays * np.clip(1 - yy / H, 0, 1) * 0.06)[..., None] * rgb("fff7e0")
    return img


def desert_dunes():
    sky = vgrad([(0, (64, 120, 178)), (0.35, (150, 186, 206)), (0.52, (244, 214, 170)), (1, (244, 214, 170))])
    img = np.broadcast_to(sky, (H, W, 3)).copy()
    img += glow(W * 0.22, H * 0.5, 650, 2.5)[..., None] * rgb("fff0c8") * 0.6
    layers = [(0.55, 40, "e3a96f", 0.45, 71), (0.63, 70, "d98d52", 0.3, 72), (0.74, 110, "c97338", 0.15, 73),
              (0.88, 160, "b45d27", 0.0, 74)]
    haze = rgb("f2d3a8")
    xs = np.arange(W)
    for base, amp, col, fog, seed in layers:
        n = fbm1d(W, seed, octaves=5, base=2, rough=0.45)
        rng = np.random.default_rng(seed)
        crest = H * base - amp * (0.6 + 0.4 * np.sin(xs / W * rng.uniform(4, 7) + rng.uniform(0, 6))) - amp * 0.5 * n
        slope = np.gradient(crest)
        lit = np.clip(0.5 - slope * 0.9, 0, 1)
        lit = np.asarray(Image.fromarray((lit * 255).astype(np.uint8)[None, :]).filter(
            ImageFilter.GaussianBlur(2)), dtype=float)[0] / 255
        yy = np.arange(H)[:, None]
        depth = np.clip((yy - crest[None, :]) / (amp * 2.2), 0, 1)
        shade = lerp(lit[None, :], 0.55, depth)
        c = lerp(rgb(col), haze, fog)
        layer = c[None, None, :] * (0.62 + 0.55 * shade[..., None])
        ripples = 0.04 * np.sin((yy - crest[None, :]) * 0.35 + fbm2d(H, W, seed + 9, base=8) * 9)[..., None]
        layer = layer * (1 + ripples * (depth[..., None] > 0))
        m = coverage(crest)
        img = img * (1 - m) + layer * m
    return img


def ocean_sunset():
    horizon = int(H * 0.6)
    sky = vgrad([(0, (40, 52, 110)), (0.3, (120, 90, 150)), (0.5, (240, 140, 110)), (0.6, (255, 190, 120)),
                 (1, (255, 190, 120))])
    img = np.broadcast_to(sky, (H, W, 3)).copy()
    sx, sy = W * 0.5, horizon - 70
    img += glow(sx, sy, 900, 2.4)[..., None] * rgb("ffd59a") * 0.6
    img = np.where(glow(sx, sy, 62, 0.3)[..., None] > 0.2, rgb("fff2d0"), img)
    clouds = fbm2d(H, W, 81, octaves=6, base=3, stretch=(1, 4))
    cl = np.clip((clouds - 0.5) * 4, 0, 1) * np.clip(1 - np.arange(H)[:, None] / horizon, 0, 1) ** 0.6
    ccol = lerp(rgb("5b3f6e"), rgb("ffb38a"), np.clip(np.arange(H)[:, None, None] / horizon, 0, 1))
    img = img * (1 - cl[..., None] * 0.75) + ccol * cl[..., None] * 0.75
    sea = vgrad([(0, (230, 150, 120)), (0.62, (230, 150, 120)), (0.7, (70, 70, 110)), (1, (12, 22, 48))])
    sea = np.broadcast_to(sea, (H, W, 3))
    yy, xx = np.mgrid[horizon:H, 0:W]
    t = (yy - horizon) / (H - horizon)
    width = 60 + 520 * t
    column = np.exp(-((xx - sx) / width) ** 2)
    waves = fbm2d(H - horizon, W, 82, octaves=5, base=10, stretch=(1, 8))
    sparkle = np.clip((waves - 0.55) * 6, 0, 1) * column
    water = sea[horizon:] * (0.75 + 0.35 * waves[..., None]) + sparkle[..., None] * rgb("ffe2b0") * 0.9
    water += (column * 0.25)[..., None] * rgb("ffb27a")
    img[horizon:] = water
    return img


def green_hills():
    sky = vgrad([(0, (58, 118, 190)), (0.4, (140, 186, 226)), (0.58, (236, 232, 214)), (1, (236, 232, 214))])
    img = np.broadcast_to(sky, (H, W, 3)).copy()
    img += glow(W * 0.74, H * 0.5, 520, 2.4)[..., None] * rgb("fff0c8") * 0.35
    clouds = fbm2d(H, W, 91, octaves=6, base=3, stretch=(1, 3))
    cl = np.clip((clouds - 0.55) * 3.2, 0, 1) * np.clip(1 - np.arange(H)[:, None] / (H * 0.5), 0, 1)
    img = img * (1 - cl[..., None] * 0.7) + rgb("fbfbf8") * cl[..., None] * 0.7
    layers = [(0.6, 46, "7fa58a", 0.45, 92), (0.68, 74, "5f9a58", 0.25, 93), (0.78, 104, "3f8436", 0.1, 94),
              (0.92, 130, "2b6a25", 0.0, 95)]
    haze = rgb("cfe0d8")
    yy = np.arange(H)[:, None]
    for base, amp, col, fog, seed in layers:
        n = fbm1d(W, seed, octaves=4, base=2, rough=0.4)
        ridge = H * base - amp * (0.5 + 0.5 * n)
        slope = np.gradient(ridge)
        lit = np.clip(0.55 - slope * 0.6, 0.2, 1)[None, :]
        depth = np.clip((yy - ridge[None, :]) / 200, 0, 1)
        c = lerp(rgb(col), haze, fog)
        light = (0.9 + 0.3 * lit) * (1.05 - 0.3 * depth)
        texture = 0.93 + 0.14 * fbm2d(H, W, seed + 5, base=14)
        layer = c[None, None, :] * (light * texture)[..., None]
        m = coverage(ridge)
        img = img * (1 - m) + layer * m
    # A lone tree on the nearest hill.
    S = 3
    mask = Image.new("L", (W * S, H * S), 0)
    d = ImageDraw.Draw(mask)
    tx, ty = 1380, 905
    d.rectangle([(tx - 7) * S, (ty - 70) * S, (tx + 7) * S, (ty + 8) * S], fill=255)
    rng = np.random.default_rng(96)
    for _ in range(26):
        cx = tx + rng.normal(0, 46)
        cy = ty - 120 + rng.normal(0, 26)
        r = rng.uniform(28, 46)
        d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=255)
    m = np.asarray(mask.resize((W, H), Image.LANCZOS), dtype=float)[..., None] / 255
    img = img * (1 - m) + rgb("1d3a18") * m
    return img


SCENES = [
    ("01-alpine-dusk", alpine_dusk),
    ("02-aurora-over-the-lake", aurora_lake),
    ("03-misty-forest", misty_forest),
    ("04-desert-dunes", desert_dunes),
    ("05-ocean-sunset", ocean_sunset),
    ("06-green-hills", green_hills),
]


def font(size, bold=False):
    for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/Library/Fonts/Arial.ttf", "C:/Windows/Fonts/arial.ttf"]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def icons(first, res_dir):
    os.makedirs(os.path.join(res_dir, "drawable-xhdpi"), exist_ok=True)
    # Banner 320x180: the first wallpaper with the name on it.
    banner = first.resize((320, 180), Image.LANCZOS).convert("RGBA")
    shade = Image.new("RGBA", banner.size, (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle([0, 110, 320, 180], fill=(0, 0, 0, 90))
    banner = Image.alpha_composite(banner, shade.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(banner)
    f = font(38, bold=True)
    tw = d.textlength("TV Home", font=f)
    d.text(((320 - tw) / 2, 118), "TV Home", font=f, fill=(255, 255, 255, 255))
    banner.convert("RGB").save(os.path.join(res_dir, "drawable-xhdpi", "banner.png"), optimize=True)
    # Icon 96x96: rounded square crop of the same scene.
    crop = first.crop((660, 180, 1560, 1080)).resize((96, 96), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (96 * 4, 96 * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, 96 * 4 - 1, 96 * 4 - 1], radius=22 * 4, fill=255)
    crop.putalpha(mask.resize((96, 96), Image.LANCZOS))
    crop.save(os.path.join(res_dir, "drawable-xhdpi", "icon.png"), optimize=True)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    out = os.path.join(root, "assets", "wallpapers")
    os.makedirs(out, exist_ok=True)
    first = None
    for name, fn in SCENES:
        im = save(fn(), os.path.join(out, name + ".jpg"))
        first = first or im
        print("wrote", name)
    icons(first, os.path.join(root, "res"))
    print("wrote icon and banner")


if __name__ == "__main__":
    main()
