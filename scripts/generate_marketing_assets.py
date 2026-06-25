"""Generate marketing assets for the README and GitHub social preview.

This script is intentionally separate from the runtime examples. It uses Pillow
only to render static promotional assets; the project itself does not require
Pillow to run Mini Claude Code.
"""

from __future__ import annotations

from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

FONT_SANS = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_MONO = "/System/Library/Fonts/Menlo.ttc"

BG = (10, 14, 28)
CARD = (17, 24, 39)
CARD_2 = (15, 23, 42)
TEXT = (242, 247, 255)
MUTED = (148, 163, 184)
GREEN = (34, 197, 94)
CYAN = (56, 189, 248)
BLUE = (99, 102, 241)
YELLOW = (250, 204, 21)
RED = (248, 113, 113)


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def rounded_rectangle(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def add_glow(
    image: Image.Image,
    xy: tuple[int, int, int, int],
    color: tuple[int, int, int],
    radius: int,
) -> None:
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(xy, fill=(*color, 80))
    glow = glow.filter(ImageFilter.GaussianBlur(radius))
    image.alpha_composite(glow)


def draw_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    color: tuple[int, int, int],
) -> int:
    label_font = font(FONT_SANS, 26)
    bbox = draw.textbbox((0, 0), text, font=label_font)
    width = bbox[2] - bbox[0] + 32
    rounded_rectangle(draw, (x, y, x + width, y + 46), 23, (20, 31, 53), color, 1)
    draw.text((x + 16, y + 9), text, font=label_font, fill=color)
    return x + width + 14


def draw_terminal(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    lines: list[tuple[str, tuple[int, int, int]]],
) -> None:
    x1, y1, x2, y2 = xy
    rounded_rectangle(draw, xy, 24, CARD, (51, 65, 85), 1)
    draw.ellipse((x1 + 28, y1 + 26, x1 + 46, y1 + 44), fill=RED)
    draw.ellipse((x1 + 58, y1 + 26, x1 + 76, y1 + 44), fill=YELLOW)
    draw.ellipse((x1 + 88, y1 + 26, x1 + 106, y1 + 44), fill=GREEN)
    draw.text((x1 + 130, y1 + 22), "examples/chapter-04", font=font(FONT_MONO, 22), fill=MUTED)

    mono = font(FONT_MONO, 23)
    y = y1 + 74
    for line, color in lines:
        draw.text((x1 + 34, y), line, font=mono, fill=color)
        y += 35


def generate_social_preview() -> Path:
    ASSETS.mkdir(exist_ok=True)
    image = Image.new("RGBA", (1280, 640), BG)
    draw = ImageDraw.Draw(image)

    add_glow(image, (820, -140, 1380, 420), BLUE, 90)
    add_glow(image, (-180, 320, 420, 820), CYAN, 90)

    draw.text(
        (72, 70),
        "Mini Claude Code",
        font=font(FONT_SANS, 74),
        fill=TEXT,
    )
    draw.text(
        (76, 150),
        "in Python",
        font=font(FONT_SANS, 48),
        fill=CYAN,
    )

    subtitle = "读一百遍Claude Code解读，不如自己写一遍"
    draw.text((76, 226), subtitle, font=font(FONT_SANS, 34), fill=(226, 232, 240))

    x = 76
    for label, color in [
        ("Agent Loop", CYAN),
        ("Tool Use", GREEN),
        ("System Prompt", YELLOW),
        ("CLI & Session", BLUE),
    ]:
        x = draw_badge(draw, x, 300, label, color)

    terminal_lines = [
        ("$ python agent.py \"read tools.py\"", GREEN),
        ("tool: read_file -> tools.py", YELLOW),
        ("tool_result -> messages -> next LLM call", CYAN),
        ("OK Chapter 4 complete - 24 tests passed", GREEN),
    ]
    draw_terminal(draw, (74, 390, 1206, 604), terminal_lines)

    draw.text(
        (850, 74),
        "github.com/Xiaxia1997",
        font=font(FONT_MONO, 24),
        fill=MUTED,
    )
    draw.text(
        (827, 110),
        "mini-claude-code-python",
        font=font(FONT_MONO, 26),
        fill=(203, 213, 225),
    )

    output = ASSETS / "social-preview.png"
    image.convert("RGB").save(output, quality=95)
    return output


def draw_demo_frame(
    progress: int,
    lines: list[tuple[str, tuple[int, int, int]]],
) -> Image.Image:
    image = Image.new("RGBA", (1120, 660), BG)
    draw = ImageDraw.Draw(image)
    add_glow(image, (720, -130, 1260, 380), BLUE, 80)
    add_glow(image, (-220, 360, 420, 920), CYAN, 80)

    rounded_rectangle(draw, (48, 42, 1072, 618), 28, CARD_2, (51, 65, 85), 1)
    draw.ellipse((80, 72, 100, 92), fill=RED)
    draw.ellipse((112, 72, 132, 92), fill=YELLOW)
    draw.ellipse((144, 72, 164, 92), fill=GREEN)
    draw.text((190, 68), "Mini Claude Code · Chapter 4", font=font(FONT_MONO, 26), fill=MUTED)

    title = "不用框架，手写Claude Code的消息流"
    draw.text((82, 118), title, font=font(FONT_SANS, 34), fill=TEXT)

    mono = font(FONT_MONO, 24)
    y = 178
    for line, color in lines[:progress]:
        for wrapped in textwrap.wrap(line, width=66, replace_whitespace=False):
            draw.text((84, y), wrapped, font=mono, fill=color)
            y += 34
        y += 4

    draw.text(
        (82, 560),
        "Agent Loop / Tool Use / System Prompt / CLI & Session",
        font=font(FONT_SANS, 25),
        fill=(203, 213, 225),
    )
    draw.text(
        (82, 594),
        "github.com/Xiaxia1997/mini-claude-code-python",
        font=font(FONT_MONO, 21),
        fill=CYAN,
    )
    return image.convert("P", palette=Image.Palette.ADAPTIVE)


def generate_demo_gif() -> Path:
    ASSETS.mkdir(exist_ok=True)
    transcript = [
        ("$ cd examples/chapter-04", GREEN),
        ("$ python agent.py \"读tools.py，解释工具执行流程\"", GREEN),
        ("tool call:", MUTED),
        ("  read_file -> tools.py", YELLOW),
        ("tool_result: def read_file(...), execute_tool(...)", CYAN),
        ("Mini Claude Code: save tool_use into messages", TEXT),
        ("then run local tool and send tool_result back", TEXT),
        ("$ python agent.py --resume", GREEN),
        ("Session restored (4 messages).", CYAN),
        ("OK Resume works: messages are saved by the program.", GREEN),
    ]

    frames: list[Image.Image] = []
    durations: list[int] = []
    for progress in range(1, len(transcript) + 1):
        frames.append(draw_demo_frame(progress, transcript))
        durations.append(600 if progress < len(transcript) else 1600)

    output = ASSETS / "demo.gif"
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    return output


def main() -> None:
    social = generate_social_preview()
    demo = generate_demo_gif()
    print(f"generated {social.relative_to(ROOT)}")
    print(f"generated {demo.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
