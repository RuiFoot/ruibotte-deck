"""RuiBotte Deck 아이콘 생성 (Pillow): 남보라 → 보라 → 주황 그라데이션 둥근 타일 + 흰 맥박선(심전도) + 끝의 '실시간' 점.
결과:
  assets/icon.ico, assets/icon.png        앱 아이콘 (exe·바로가기·설치 프로그램) — 밝은/어두운 배경 모두 잘 보이는 색 타일
  assets/tray-dark.png, tray-light.png    트레이 아이콘 (배경 없음): 어두운 작업 표시줄 = 흰 선, 밝은 작업 표시줄 = 진회색 선
  frontend/public/icon-192.png, icon-512.png   브라우저·앱 창
실행: py make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
S = 1024                      # 크게 그린 뒤 줄여서 테두리를 부드럽게

# 맥박선 꼭짓점 (0~1 좌표): 평평 → 작은 언덕 → 큰 스파이크 → 아래로 꺾임 → 평평
PULSE = [(0.20, 0.54), (0.33, 0.54), (0.385, 0.46), (0.44, 0.54), (0.48, 0.54),
         (0.545, 0.27), (0.62, 0.76), (0.675, 0.54), (0.76, 0.54)]
# 트레이(16~32px)용: 작은 언덕을 빼고 굵게, 칸을 가득 채움
PULSE_TRAY = [(0.04, 0.55), (0.30, 0.55), (0.45, 0.10), (0.62, 0.92), (0.74, 0.55), (0.84, 0.55)]
ACCENT = (255, 138, 76)       # 끝 점 (주황)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient_tile(size):
    """대각선 그라데이션: 왼쪽 위 남보라 → 보라 → 오른쪽 아래 주황빛."""
    stops = [(0.0, (67, 56, 202)), (0.55, (124, 58, 237)), (1.0, (234, 88, 12))]
    g = Image.new("RGBA", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            t = t ** 1.35          # 주황은 모서리에만 살짝
            for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
                if t <= p1:
                    px[x, y] = (*lerp(c0, c1, (t - p0) / (p1 - p0)), 255)
                    break
    return g


def pulse_layer(size, color, width, dot=True, glow=False, shape=PULSE):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(x * size, y * size) for x, y in shape]
    d.line(pts, fill=color, width=width, joint="curve")
    r = width / 2
    for x, y in (pts[0], pts[-1]):                  # 둥근 끝
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)
    if dot:
        x, y = pts[-1]
        R = width * 0.95
        d.ellipse([x - R, y - R, x + R, y + R], fill=(*ACCENT, 255))
    if glow:                                          # 선 뒤의 은은한 빛
        halo = img.filter(ImageFilter.GaussianBlur(size * 0.025))
        img = Image.alpha_composite(halo, img)
    return img


def app_icon():
    tile = gradient_tile(S // 4).resize((S, S), Image.LANCZOS)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([48, 48, S - 48, S - 48], radius=232, fill=255)
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    base.paste(tile, (0, 0), mask)
    return Image.alpha_composite(base, pulse_layer(S, (255, 255, 255, 255), 64, glow=True))


def tray_icon(color):
    """작은 크기에서도 보이게 굵은 선, 여백 거의 없이."""
    return pulse_layer(S, color, 128, shape=PULSE_TRAY)


def main():
    big = app_icon()
    big.resize((512, 512), Image.LANCZOS).save(ROOT / "assets" / "icon.png")
    big.save(ROOT / "assets" / "icon.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    for n in (192, 512):
        big.resize((n, n), Image.LANCZOS).save(ROOT / "frontend" / "public" / f"icon-{n}.png")
    tray_icon((255, 255, 255, 255)).resize((64, 64), Image.LANCZOS).save(ROOT / "assets" / "tray-dark.png")
    tray_icon((38, 43, 56, 255)).resize((64, 64), Image.LANCZOS).save(ROOT / "assets" / "tray-light.png")
    print("ok")


if __name__ == "__main__":
    main()
