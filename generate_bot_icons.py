"""Genera los 2 iconos PNG necesarios para el manifest de Teams.

- color.png (192x192): icono a color con el verde de Biodegradables
- outline.png (32x32): silueta blanca con fondo transparente

Uso:
    python generate_bot_icons.py

Después puedes reemplazar los PNG con diseños más bonitos si quieres,
pero estos cumplen con los requisitos técnicos de Teams.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
COLOR_PATH = ROOT / "color.png"
OUTLINE_PATH = ROOT / "outline.png"

GREEN = (14, 124, 57)        # mismo verde que el correo
GREEN_DARK = (10, 90, 40)
WHITE = (255, 255, 255)


def _try_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Intenta cargar una fuente bold; si falla, usa la default."""
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/seguibl.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_color_icon() -> None:
    """192x192 color icon: círculo verde con 'BE' en blanco."""
    size = 192
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Círculo verde con borde más oscuro
    margin = 6
    d.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=GREEN,
        outline=GREEN_DARK,
        width=4,
    )

    # Texto "BE" centrado
    font = _try_font(96)
    text = "BE"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    d.text((tx, ty), text, fill=WHITE, font=font)

    img.save(COLOR_PATH, "PNG")
    print(f"OK: {COLOR_PATH} ({size}x{size})")


def make_outline_icon() -> None:
    """32x32 outline icon: silueta blanca con fondo transparente.
    Requisito de Teams: solo usar blanco sobre transparente."""
    size = 32
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Círculo blanco con borde
    margin = 2
    d.ellipse(
        [margin, margin, size - margin, size - margin],
        outline=WHITE,
        width=2,
    )

    # Texto "B" en blanco centrado
    font = _try_font(18)
    text = "B"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    d.text((tx, ty), text, fill=WHITE, font=font)

    img.save(OUTLINE_PATH, "PNG")
    print(f"OK: {OUTLINE_PATH} ({size}x{size})")


if __name__ == "__main__":
    make_color_icon()
    make_outline_icon()
    print("\nIconos listos. Para empaquetar el manifest:")
    print("  Compress-Archive -Path manifest.json,color.png,outline.png -DestinationPath teams_bot.zip")
