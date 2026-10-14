#!/usr/bin/env python3
"""Generate the Syntexophobic icon set in phobiCCCP's icon style.

phobiCCCP draws its tool icons on a 20x20 grid with a small fixed colour language,
and these SVGs follow it to the letter (same grid, same widths, same colours, and the
same drawings wherever both apps have the tool):

* INK   the shape itself: 1.6 px strokes, and solid fills for "the selected/source" part.
* BLUE  the points you click (r 1.7 dots) and helper geometry (1.0 px, often dashed:
        radii, construction circles, axes, the sketch profile a feature is built from).
* AMBER where material goes: toolpaths, cuts, pockets, filled areas.
* RED   what gets thrown away (the trimmed part of a line).

`mono_icon()` swaps INK for the ink colour and greys everything for the disabled copy.
"""
from pathlib import Path
import sys

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("src/syntexophobic/assets/icons")

INK = "currentColor"
BLUE = "#9fc8f2"
AMBER = "#f2a536"
RED = "#e05050"

HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none" '
        f'stroke="{INK}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">')


def dots(*pts, colour=BLUE, r=1.7):
    """Clicked points: filled blue dots, as in phobiCCCP's sketch-tool variants."""
    return "".join(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{colour}" stroke="none"/>' for x, y in pts)


def helper(inner, dashed=True):
    """Thin blue helper geometry (Qt::DashLine at 1 px is a 4-on, 2-off dash)."""
    dash = ' stroke-dasharray="4 2" stroke-linecap="butt"' if dashed else ""
    return f'<g stroke="{BLUE}" stroke-width="1"{dash}>{inner}</g>'


def cut(inner, width=1.6, dashed=False):
    dash = ' stroke-dasharray="4 2"' if dashed else ""
    return f'<g stroke="{AMBER}" stroke-width="{width}"{dash}>{inner}</g>'


def filled(inner, colour=INK, opacity=None):
    op = f' fill-opacity="{opacity}"' if opacity else ""
    return f'<g fill="{colour}"{op}>{inner}</g>'


def dotted(inner, colour=INK, width=1.5):
    """Qt::DotLine: 1-on, 2-off in pen widths."""
    return (f'<g stroke="{colour}" stroke-width="{width}" stroke-linecap="butt" '
            f'stroke-dasharray="{width} {2 * width}">{inner}</g>')


ARROW = "M6 3 6 15 9.5 11.5 12 16 13.8 15 11.4 10.6 15 10z"
HEX = "M17.5 10 13.75 16.5 6.25 16.5 2.5 10 6.25 3.5 13.75 3.5z"        # flat top, r 7.5
SLOT = '<rect x="2.5" y="6" width="15" height="8" rx="4"/>'
# phobiCCCP's isometric "Preview" cube and its three visible faces
CUBE = '<path d="M10 2.5 16.5 6.2V13.8L10 17.5 3.5 13.8V6.2Z"/><path d="M3.5 6.2 10 10 16.5 6.2M10 10V17.5"/>'
TOP_FACE = "M10 2.5 16.5 6.2 10 10 3.5 6.2Z"
LEFT_FACE = "M3.5 6.2 10 10V17.5L3.5 13.8Z"
RIGHT_FACE = "M16.5 6.2 10 10V17.5L16.5 13.8Z"
PAGE = '<path d="M5 2.5H12L16 6.5V17.5H5Z"/><path d="M12 2.5V6.5H16"/>'


def face(d, colour=BLUE):
    return f'<path d="{d}" fill="{colour}" fill-opacity="0.55" stroke="none"/>'


# name -> inner SVG markup. Grouped by where they appear.
ICONS = {
    # ---- application / navigation -------------------------------------------------
    "new": PAGE + '<path d="M10.5 9.5V15.5M7.5 12.5H13.5"/>',
    "open": '<path d="M2.5 5.5A1 1 0 0 1 3.5 4.5H7.5L9.5 6.5H16.5A1 1 0 0 1 17.5 7.5V15.5'
            'A1 1 0 0 1 16.5 16.5H3.5A1 1 0 0 1 2.5 15.5Z"/><path d="M2.5 9H17.5"/>',
    "design": helper('<circle cx="8" cy="11" r="5"/>') + '<path d="M2.5 17.5H17.5"/>'
              '<path d="M8 14.5 9 11 15.5 4.5 17.5 6.5 11 13Z"/>' + dots((8, 11)),
    "export": PAGE + '<path d="M10.5 8.5V15M8 12.5 10.5 15 13 12.5"/>',
    "generate": cut('<path d="M3 5C5 3 7 7 9 5S13 3 15 5 17 7 17 5"/>'
                    '<path d="M3 9C5 7 7 11 9 9S13 7 15 9 17 11 17 9"/>')
                + '<path d="M10 12V18M7.5 15.5 10 18 12.5 15.5"/>',
    "simulate": '<circle cx="10" cy="10" r="7.5"/>'
                + filled('<path d="M8 6.5 14 10 8 13.5Z"/>'),
    "toolpaths": '<rect x="3" y="3" width="14" height="14"/>'
                 + f'<path d="M6 6H14V10H6V14H14" stroke="#f0a030"/>',
    "rapids": helper('<path d="M3.5 16.5 16.5 3.5"/>') + dots((3.5, 16.5), (16.5, 3.5)),
    "iso": CUBE,
    "model": '<path d="M2 15C6 15 7 5 10 5S14 15 18 15"/><path d="M2 17.5H18"/>',
    # view presets: phobiCCCP's cube with the face it looks at shaded blue
    "top": face(TOP_FACE) + CUBE,
    "front": face(LEFT_FACE) + CUBE,
    "right": face(RIGHT_FACE) + CUBE,
    "fit": '<rect x="4" y="4" width="12" height="12"/><path d="M8 10H12M10 8V12"/>',
    "finish": '<circle cx="10" cy="10" r="7.5"/><path d="M6.5 10.3 9 12.8 13.8 7.6"/>',
    "up": '<path d="M5 12.5 10 7.5 15 12.5"/>',
    "down": '<path d="M5 7.5 10 12.5 15 7.5"/>',

    # ---- sketch: create -----------------------------------------------------------
    "select": filled(f'<path d="{ARROW}"/>'),
    "line": '<path d="M4 16 16 4"/>' + dots((4, 16), (16, 4)),
    "rectangle": '<rect x="4" y="5" width="12" height="10"/>' + dots((4, 5), (16, 15)),
    "circle": '<circle cx="10" cy="10" r="6"/>' + helper('<path d="M10 10H16"/>') + dots((10, 10)),
    "arc": '<path d="M3.42 9.61A7 7 0 0 1 16.58 9.61"/>' + dots((3.4, 9.6), (10, 5), (16.6, 9.6)),
    "spline": '<path d="M2.5 14C5 4 9 4 10 10S15 16 17.5 6"/>' + dots((2.5, 14), (10, 10), (17.5, 6)),
    "polygon": helper('<circle cx="10" cy="10" r="7.5"/>') + f'<path d="{HEX}"/>',
    "slot": SLOT + helper('<path d="M6.5 10H13.5"/>') + dots((6.5, 10), (13.5, 10)),
    "text": '<path d="M4.5 4H15.5M10 4V16.5" stroke-width="2.4" stroke-linecap="square"/>',
    "point": '<path d="M5 5 15 15M5 15 15 5"/>' + dots((10, 10), r=1.4),
    "trim": '<path d="M10 2V18M2 10H10"/>' + dotted('<path d="M10 10H18"/>', RED, 1.6),

    # ---- sketch: modify -----------------------------------------------------------
    "fillet": dotted('<path d="M3 10V3H10"/>', RED, 1.2) + '<path d="M3 17V10A7 7 0 0 1 10 3H17"/>',
    "chamfer": dotted('<path d="M3 9V3H9"/>', RED, 1.2) + '<path d="M3 17V9L9 3H17"/>',
    "offset": '<rect x="6" y="6" width="8" height="8" rx="1.5"/>'
              + dotted('<rect x="2.5" y="2.5" width="15" height="15" rx="4"/>'),
    "mirror": helper('<path d="M10 2V18"/>') + filled('<path d="M8 4V16H2Z"/>') + '<path d="M12 4V16H18Z"/>',
    "dimension": '<path d="M3 15 17 5M1.5 12.8 4.5 17.2M15.5 2.8 18.5 7.2"/>',
    "construction": helper('<path d="M3 17 17 3"/><circle cx="10" cy="10" r="5"/>') + dots((3, 17), (17, 3)),
    "trace": '<rect x="2.5" y="3.5" width="15" height="13" rx="1"/>'
             + cut('<path d="M2.5 14 7 9.5 10 12.5 13 8.5 17.5 13"/>') + dots((6.5, 7), r=1.4, colour=INK),
    "dxf": PAGE + '<path d="M7.5 14.5 9.5 11.5 11.5 13 13.5 9.5"/>' + dots((7.5, 14.5), (13.5, 9.5), r=1.3),

    # ---- sketch: constraints (the geometry in ink, what the constraint holds in blue) -
    "coincident": '<path d="M3 15 10 10 17 13"/>' + dots((10, 10), r=2.2),
    "horizontal": '<path d="M3 10H17"/>' + helper('<path d="M3 6V14M17 6V14"/>', dashed=False) + dots((3, 10), (17, 10)),
    "vertical": '<path d="M10 3V17"/>' + helper('<path d="M6 3H14M6 17H14"/>', dashed=False) + dots((10, 3), (10, 17)),
    "parallel": '<path d="M4 17 10 3M10 17 16 3"/>' + helper('<path d="M2 9.5 18 11.5"/>'),
    "perpendicular": '<path d="M4 3V16H17"/>' + helper('<path d="M4 11H9V16"/>', dashed=False),
    "equal": '<path d="M3 6H17M3 14H17"/>' + helper('<path d="M9 3.5 11 8.5M9 11.5 11 16.5"/>', dashed=False),
    "tangent": '<circle cx="9" cy="11.5" r="5.5"/><path d="M2 6H18"/>' + dots((9, 6)),
    "concentric": '<circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="3.5"/>' + dots((10, 10), r=1.4),
    "midpoint": '<path d="M3 10H17"/>' + dots((3, 10), (17, 10), colour=INK, r=1.3) + dots((10, 10), r=2.2),
    "point_on": '<path d="M3 14 17 6"/>' + dots((10, 10), r=2.2),
    "fixed": '<path d="M10 3V13M6.5 6 10 3 13.5 6"/>' + '<path d="M4 17H16"/>'
             + helper('<path d="M6 17 4.5 19M10 17 8.5 19M14 17 12.5 19"/>', dashed=False) + dots((10, 13)),
    "symmetric": helper('<path d="M10 2V18"/>') + dots((4.5, 10), (15.5, 10), colour=INK, r=1.8)
                 + '<path d="M7.5 7 5.5 10 7.5 13M12.5 7 14.5 10 12.5 13"/>',

    # ---- design features (the sketch profile in blue, the solid in ink, cuts amber) ---
    "extrude": face(LEFT_FACE) + '<path d="M3.5 13.8 10 17.5 16.5 13.8"/>'
               + helper('<path d="M3.5 13.8 10 10 16.5 13.8"/>', dashed=False)
               + '<path d="M3.5 13.8V8.5M10 17.5V12M16.5 13.8V8.5M3.5 8.5 10 12 16.5 8.5 10 4.7Z"/>'
               + '<path d="M10 9V1.8M8 3.8 10 1.8 12 3.8" stroke-width="1.3"/>',
    "revolve": helper('<path d="M10 1.5V18.5"/>') + '<path d="M12 5H15V15H12Z"/>'
               + '<path d="M8 4.5A6.5 5.5 0 0 0 8 15.5"/><path d="M8 15.5 5.8 15M8 15.5 7.2 13.4"/>',
    "loft": helper('<ellipse cx="10" cy="15.5" rx="7" ry="2.3"/><ellipse cx="10" cy="4.5" rx="3.5" ry="1.4"/>',
                   dashed=False)
            + '<path d="M3 15.5 6.5 4.5M17 15.5 13.5 4.5"/>',
    "sweep": helper('<path d="M4 16C9 16 7 5 12 5H18"/>') + '<path d="M2 12.5H6V17.5H2Z"/>'
             + '<path d="M14 2.5 16 5 14 7.5"/>',
    "shell": '<rect x="3" y="4" width="14" height="13"/>'
             + filled('<rect x="6" y="4" width="8" height="10"/>', AMBER, 0.35)
             + cut('<path d="M6 4V14H14V4"/>', 1.4),
    "hole": '<circle cx="10" cy="10" r="7.5"/>'
            + filled('<circle cx="10" cy="10" r="3.2"/>', AMBER, 0.35) + cut('<circle cx="10" cy="10" r="3.2"/>', 1.4)
            + helper('<path d="M10 1V5M10 15V19M1 10H5M15 10H19"/>', dashed=False),
    "relief": '<path d="M2 15C6 15 7 5 10 5S14 15 18 15"/><path d="M2 17.5H18"/>'
              + cut('<path d="M4 15C6 15 8 8 10 8S14 15 16 15"/>', 1.2, dashed=True),
    "import": face(TOP_FACE) + CUBE.replace("M3.5 6.2 10 10 16.5 6.2M10 10V17.5", "M10 10V17.5")
              + '<path d="M3.5 6.2 10 10 16.5 6.2"/>' + helper('<path d="M10 0.5V7"/>', dashed=False),
    "sketch_new": '<path d="M2 15 6 6H18L14 15Z"/>'
                  + helper('<circle cx="10" cy="10.5" r="2.6"/><path d="M5.5 13 15.5 8"/>', dashed=False)
                  + dots((10, 10.5), r=1.2),
    "sketch_face": face(TOP_FACE) + CUBE + helper('<path d="M6.8 6.2 10 8.1 13.2 6.2 10 4.4Z"/>', dashed=False)
                   + dots((10, 6.2), r=1.2),
    "parameters": '<path d="M3 5H17M3 10H17M3 15H17"/>'
                  + filled('<circle cx="7" cy="5" r="1.8"/><circle cx="13" cy="10" r="1.8"/>'
                           '<circle cx="10" cy="15" r="1.8"/>'),
    "remove": '<path d="M4 6H16M8 6V4H12V6"/><path d="M5.5 6 6.5 17H13.5L14.5 6"/>'
              '<path d="M8.5 9 8.8 14M11.5 9 11.2 14"/>',
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, inner in ICONS.items():
        (OUT / f"{name}.svg").write_text(HEAD + inner + "</svg>\n")
    print(f"Wrote {len(ICONS)} icons to {OUT}")


if __name__ == "__main__":
    main()
