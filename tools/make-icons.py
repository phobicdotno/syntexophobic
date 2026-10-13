#!/usr/bin/env python3
"""Generate the Syntexophobic icon set: monochrome `currentColor` line icons in the
phobiCCCP drafting style (clean geometric strokes, round caps, construction-node dots).

All icons share a 24x24 grid, 1.6 px strokes and round joins so they read at 16 px and
scale to 64. `mono_icon()` recolours `currentColor` to the palette text colour, so the
art stays single-colour; the style is in the linework, not the colour.
"""
from pathlib import Path
import sys

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("src/syntexophobic/assets/icons")

HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">')


def dot(x, y, r=1.15):
    """A filled construction node (the logo's gold-ring node, in one colour)."""
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="currentColor" stroke="none"/>'


def ring(x, y, r=1.6):
    return f'<circle cx="{x}" cy="{y}" r="{r}"/>'


def dash(inner):
    return inner.replace("<path ", '<path stroke-dasharray="2.4 2.2" ', 1)


# name -> inner SVG markup. Grouped by where they appear.
ICONS = {
    # ---- application / navigation -------------------------------------------------
    "new": '<path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z"/>'
           '<path d="M13 3v6h6"/><path d="M12 12v5M9.5 14.5h5"/>',
    "open": '<path d="M3 7a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>'
            '<path d="M3 10h18"/>',
    "design": '<path d="M3.5 20.5h17"/><path d="M5 17.5l1-4 10-10 3 3-10 10z"/><path d="M14 5.5l3 3"/>'
              + dot(15.5, 7),
    "export": '<path d="M12 3v11"/><path d="M8 10l4 4 4-4"/><path d="M4 18v2a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-2"/>',
    "generate": '<path d="M4 7c2.5-2.5 5 2.5 7.5 0S16.5 4.5 20 7"/>'
                '<path d="M4 12c2.5-2.5 5 2.5 7.5 0S16.5 9.5 20 12"/>'
                '<path d="M12 15v6M9 18l3 3 3-3"/>',
    "simulate": '<path d="M6 4.5l13 7.5-13 7.5z"/>',
    "toolpaths": '<path d="M3 6c3-3 6 3 9 0s6-3 9 0"/><path d="M3 12c3-3 6 3 9 0s6-3 9 0"/>'
                 '<path d="M3 18c3-3 6 3 9 0s6-3 9 0"/>',
    "rapids": dash('<path d="M4 18 L20 6"/>') + dot(4, 18) + dot(20, 6),
    "iso": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 3v9l8 4.5M12 12l-8 4.5"/>',
    "top": '<path d="M4 8h16v12H4z"/><path d="M4 8l4-4h16l-4 4"/><path d="M20 8l4-4"/>'
           '<path d="M8 4v12"/><path d="M4 8h16"/>',
    "model": '<path d="M12 2.5l8.5 4.9v9.2L12 21.5l-8.5-4.9V7.4z"/>'
             '<path d="M12 2.5v19M3.5 7.4 12 12l8.5-4.6"/>',

    # ---- sketch: create -----------------------------------------------------------
    "select": '<path d="M5 3l0 15 3.8-3.6 2.3 5.2 2.2-1-2.3-5.1H16z" fill="currentColor" stroke="none"/>'
              '<path d="M5 3l0 15 3.8-3.6 2.3 5.2 2.2-1-2.3-5.1H16z"/>',
    "line": '<path d="M5 19L19 5"/>' + dot(5, 19) + dot(19, 5),
    "rectangle": '<path d="M4 6h16v12H4z"/>' + dot(4, 6) + dot(20, 18),
    "circle": '<circle cx="12" cy="12" r="8"/>' + dot(12, 12),
    "arc": '<path d="M4 18A10 10 0 0 1 20 18"/>' + dot(4, 18) + dot(20, 18) + dot(12, 18, 1),
    "spline": '<path d="M3 18C7 6 10 20 13 12S18 4 21 8"/>' + dot(3, 18) + dot(13, 12) + dot(21, 8),
    "polygon": '<path d="M12 3l7.8 4.5v9L12 21l-7.8-4.5v-9z"/>',
    "slot": '<path d="M8 8h8a4 4 0 0 1 0 8H8a4 4 0 0 1 0-8z"/>' + dot(8, 12) + dot(16, 12),
    "text": '<path d="M6 19L12 5l6 14"/><path d="M8.3 14h7.4"/>',
    "point": '<path d="M12 4v5M12 15v5M4 12h5M15 12h5"/>' + dot(12, 12, 1.5),
    "trim": '<circle cx="6" cy="17" r="2.4"/><circle cx="6" cy="7" r="2.4"/>'
            '<path d="M8 15.5L20 6M8 8.5L20 18"/>',

    # ---- sketch: modify -----------------------------------------------------------
    "fillet": '<path d="M5 19V13A8 8 0 0 1 13 5H19"/>' + dash('<path d="M5 5H13"/>') + dash('<path d="M5 5V13"/>'),
    "offset": '<path d="M8 8h8v8H8z"/>' + dash('<path d="M4 4h16v16H4z"/>'),
    "mirror": '<path d="M4 5l5.5 7L4 19z"/><path d="M20 5l-5.5 7L20 19z"/>' + dash('<path d="M12 3v18"/>'),
    "dimension": '<path d="M4 8v8M20 8v8"/><path d="M6 12h12"/><path d="M6 12l2.5-2M6 12l2.5 2"/>'
                 '<path d="M18 12l-2.5-2M18 12l-2.5 2"/>',
    "construction": '<path stroke-dasharray="2.4 2.2" d="M4 18L20 6"/>'
                    '<path stroke-dasharray="2.4 2.2" d="M15 6a4.5 4.5 0 1 1-6 6"/>',
    "trace": '<path d="M4 5h16v14H4z"/><circle cx="8.5" cy="9" r="1.5"/>'
             '<path d="M4 17l5-5 3.5 3.5L16 11l4 4"/>',
    "dxf": '<path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z"/>'
           '<path d="M13 3v6h6"/><path d="M8 17l3-3 2 2 3-4"/>' + dot(8, 17, 0.9) + dot(16, 12, 0.9),
    "fit": '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>'
           + ring(12, 12, 2),
    "finish": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.8 2.8L16.5 9"/>',

    # ---- sketch: constraints ------------------------------------------------------
    "coincident": dot(10, 12, 2.3) + '<circle cx="14" cy="12" r="3.2"/>',
    "horizontal": '<path d="M4 12h16"/><path d="M4 9v6M20 9v6"/>',
    "vertical": '<path d="M12 4v16"/><path d="M9 4h6M9 20h6"/>',
    "parallel": '<path d="M7 20L12 4M13 20L18 4"/>',
    "perpendicular": '<path d="M6 4v14h14"/><path d="M6 13h5v5"/>',
    "equal": '<path d="M5 9h14M5 15h14"/>',
    "tangent": '<circle cx="9" cy="14" r="5"/><path d="M4 9h16"/>',
    "concentric": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.5"/>' + dot(12, 12, 1),
    "midpoint": '<path d="M4 12h16"/>' + dot(12, 12, 2) + dot(4, 12, 1) + dot(20, 12, 1),
    "point_on": '<path d="M4 16L20 8"/>' + dot(12, 12, 2.2),
    "fixed": '<path d="M12 4v11"/><path d="M8 7l4-3 4 3"/><path d="M6 20h12"/><path d="M12 15v5"/>',
    "symmetric": dot(6, 12, 2) + dot(18, 12, 2) + dash('<path d="M12 4v16"/>')
                 + '<path d="M9 9l-2 3 2 3M15 9l2 3-2 3"/>',

    # ---- design features ----------------------------------------------------------
    "extrude": '<path d="M5 13h8v6H5z"/>' + dash('<path d="M5 13l3-3h8l-3 3"/>') + dash('<path d="M13 19l3-3"/>')
               + '<path d="M18 11V3M15 6l3-3 3 3"/>',
    "revolve": dash('<path d="M12 3v18"/>') + '<path d="M14 5h4v14h-4z"/>'
               '<path d="M9 7a6 6 0 0 0 0 10"/><path d="M9 17l-2-1.5M9 17l1.5-2"/>',
    "loft": '<path d="M5 18h8v2H5z"/><path d="M8 5h8v2H8z"/>'
            + dash('<path d="M5 18L8 7M13 18L16 7"/>'),
    "sweep": '<path d="M5 20c6 0 3-12 9-12 3 0 4 0 6 0"/><path d="M3.5 17.5h4v4h-4z"/>'
             + dash('<path d="M5.5 17.5L5 20M7.5 17.5L8 14"/>'),
    "chamfer": '<path d="M5 19V11l6-6h8"/>' + dash('<path d="M5 5h6"/>') + dash('<path d="M5 5v6"/>'),
    "shell": '<path d="M4 6h16v14H4z"/><path d="M7.5 9.5h9v7h-9z"/>',
    "hole": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.3"/>'
            '<path d="M12 4v2.5M12 17.5V20M4 12h2.5M17.5 12H20"/>',
    "relief": '<path d="M4 18h16"/><path d="M4 18l4-7 3 4 3-6 6 9"/>' + dot(8, 11, 0.9) + dot(14, 9, 0.9),
    "import": '<path d="M14 4h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5"/><path d="M3 12h11"/><path d="M10 8l4 4-4 4"/>',
    "sketch_new": '<path d="M4 20h16"/><path d="M4 20V8l4-4h12"/>' + dash('<path d="M8 8h12"/>')
                  + '<path d="M9 17l1.5-4 6-6 2.5 2.5-6 6z"/>' + dot(16.5, 8, 0.9),
    "sketch_face": '<path d="M4 7l8-4 8 4v10l-8 4-8-4z"/>' + dash('<path d="M4 7l8 4 8-4M12 11v10"/>')
                   + '<path d="M9 15l1-2.5 4-4 1.5 1.5-4 4z"/>',
    "parameters": '<path d="M4 7h16M4 12h16M4 17h16"/>' + dot(9, 7, 2) + dot(15, 12, 2) + dot(7, 17, 2),
    "remove": '<circle cx="12" cy="12" r="8"/><path d="M8 12h8"/>',
    "up": '<path d="M6 15l6-6 6 6"/>',
    "down": '<path d="M6 9l6 6 6-6"/>',
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, inner in ICONS.items():
        (OUT / f"{name}.svg").write_text(HEAD + inner + "</svg>\n")
    print(f"Wrote {len(ICONS)} icons to {OUT}")


if __name__ == "__main__":
    main()
