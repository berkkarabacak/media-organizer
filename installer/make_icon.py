"""Generate a simple camera/folder .ico for Media Organizer using Pillow."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "media_organizer.ico"


def draw_icon(size: int) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def box(x0, y0, x1, y1, r, fill, outline=None, width=1):
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill,
                            outline=outline, width=width)

    # Background rounded square (app-tile style)
    pad = s * 0.02
    box(pad, pad, s - pad, s - pad, s * 0.18, "#1b1e24")

    # Folder tab + body (blue gradient feel via two tones)
    tab_y0, body_y0 = s * 0.26, s * 0.36
    box(s * 0.14, tab_y0, s * 0.46, body_y0 + s * 0.04, s * 0.05, "#4480f5")
    box(s * 0.14, body_y0, s * 0.86, s * 0.78, s * 0.07, "#2f6fed")

    # Camera lens on the folder
    cx, cy = s * 0.5, s * 0.57
    r_outer, r_mid, r_in = s * 0.16, s * 0.11, s * 0.06
    d.ellipse([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer], fill="#f0f4f9")
    d.ellipse([cx - r_mid, cy - r_mid, cx + r_mid, cy + r_mid], fill="#1b1e24")
    d.ellipse([cx - r_in, cy - r_in, cx + r_in, cy + r_in], fill="#57a0ff")
    # Lens glint
    gr = s * 0.025
    d.ellipse([cx - r_mid * 0.5, cy - r_mid * 0.6, cx - r_mid * 0.5 + 2 * gr,
               cy - r_mid * 0.6 + 2 * gr], fill="#cfe2ff")

    return img


def main():
    sizes = [256, 128, 64, 48, 32, 16]
    base = draw_icon(256)
    base.save(OUT, format="ICO", sizes=[(n, n) for n in sizes])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
