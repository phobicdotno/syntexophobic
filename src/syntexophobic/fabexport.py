"""Sheets for the laser or the CNC from a sliced model (fabslice).

Every part is finished for the machine first: a laser cut is grown by half the kerf so
the part comes out true size, a CNC cut gets dogbones in its inside corners so slots
seat with a round bit. Each part gets its name engraved, plates their slot numbers,
stacked layers the outline of their neighbour. Then the parts are packed onto sheets
(nesting) and every sheet is written as DXF and SVG, cuts and engraving on their own
layers and colours (red cut, blue engrave: what LightBurn and most laser software expect).
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

from . import fabtools, nesting
from .fabslice import FabPart, FabResult, Ring

CUT_COLOUR, ENGRAVE_COLOUR = "#ff0000", "#0000ff"


@dataclass
class SheetSettings:
    width: float = 600.0
    height: float = 400.0
    gap: float = 3.0              # between parts
    margin: float = 5.0           # from the sheet edge
    machine: str = "laser"        # laser | cnc
    kerf: float = 0.15            # laser beam width
    tool_diameter: float = 3.175  # CNC bit
    labels: bool = True
    label_height: float = 5.0


@dataclass
class Sheet:
    cuts: list[Ring] = field(default_factory=list)
    engrave: list[list[tuple[float, float]]] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)


@dataclass
class Finished:
    name: str
    rings: list[Ring]
    engrave: list[list[tuple[float, float]]]


def finish(part: FabPart, ss: SheetSettings) -> Finished:
    """The part as the machine cuts it, with its engraving."""
    rings = part.rings
    if ss.machine == "cnc" and ss.tool_diameter > 0:
        rings = fabtools.dogbones(rings, ss.tool_diameter)
    engrave = [list(line) for line in part.engrave]
    for text, x, y, h in part.marks:
        strokes = fabtools.label(text, 0, 0, h)
        if strokes:
            xs = [p[0] for s in strokes for p in s]
            dx = x - (min(xs) + max(xs)) / 2
            engrave += [[(px + dx, py + y) for px, py in s] for s in strokes]
    if ss.labels and part.kind != "spacer":
        engrave += fabtools.label_inside(rings, part.name, ss.label_height)
    if ss.machine == "laser" and ss.kerf > 0:     # last: the label is placed on the true size
        rings = fabtools.kerf(rings, ss.kerf)
    return Finished(part.name, rings, engrave)


def layout(res: FabResult, ss: SheetSettings) -> list[Sheet]:
    """Finish every part (every copy) and pack them onto sheets."""
    pieces: list[Finished] = []
    for part in res.parts:
        f = finish(part, ss)
        pieces += [f] * part.copies
    try:
        places = nesting.nest([f.rings for f in pieces], ss.width, ss.height, ss.gap, ss.margin)
    except ValueError as e:
        raise nesting_error(e, pieces) from None
    sheets: list[Sheet] = []
    for pl in places:
        while len(sheets) <= pl.sheet:
            sheets.append(Sheet())
        f = pieces[pl.part]
        sh = sheets[pl.sheet]
        sh.cuts += [nesting.transform(r, pl) for r in f.rings]
        sh.engrave += [nesting.transform(line, pl) for line in f.engrave]
        sh.parts.append(f.name)
    return sheets


def nesting_error(e: ValueError, pieces) -> ValueError:
    return ValueError(f"{e}. Use a bigger sheet, thinner slices or fewer of them.")


# ---- files ---------------------------------------------------------------------------------

def write(sheets: list[Sheet], ss: SheetSettings, folder: str | Path, stem: str,
          formats: tuple[str, ...] = ("dxf", "svg")) -> list[Path]:
    """One file per sheet and format: <stem>-sheet1.dxf, ... Returns the files written."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, sh in enumerate(sheets, 1):
        for fmt in formats:
            path = folder / f"{stem}-sheet{i}.{fmt}"
            (write_dxf if fmt == "dxf" else write_svg)(sh, ss, path)
            out.append(path)
    return out


def write_dxf(sheet: Sheet, ss: SheetSettings, path: str | Path) -> None:
    import ezdxf
    doc = ezdxf.new("R2010", setup=False)
    doc.units = ezdxf.units.MM
    doc.layers.add("CUT", color=1)          # red
    doc.layers.add("ENGRAVE", color=5)      # blue
    doc.layers.add("SHEET", color=8)        # grey, the stock outline: not cut
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (ss.width, 0), (ss.width, ss.height), (0, ss.height)], close=True,
                       dxfattribs={"layer": "SHEET"})
    for ring in sheet.cuts:
        msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": "CUT"})
    for line in sheet.engrave:
        if len(line) >= 2:
            msp.add_lwpolyline(line, close=False, dxfattribs={"layer": "ENGRAVE"})
    doc.saveas(str(path))


def write_svg(sheet: Sheet, ss: SheetSettings, path: str | Path) -> None:
    w, h = ss.width, ss.height

    def d(pts, closed):
        return "M" + " L".join(f"{x:.3f} {h - y:.3f}" for x, y in pts) + (" Z" if closed else "")

    cuts = "\n".join(f'<path d="{d(r, True)}"/>' for r in sheet.cuts)
    eng = "\n".join(f'<path d="{d(line, False)}"/>' for line in sheet.engrave if len(line) >= 2)
    title = html.escape(", ".join(sheet.parts))
    Path(path).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}mm" height="{h:g}mm" viewBox="0 0 {w:g} {h:g}">\n'
        f'<title>{title}</title>\n'
        f'<g id="cut" fill="none" stroke="{CUT_COLOUR}" stroke-width="0.1">\n{cuts}\n</g>\n'
        f'<g id="engrave" fill="none" stroke="{ENGRAVE_COLOUR}" stroke-width="0.1">\n{eng}\n</g>\n'
        '</svg>\n', encoding="utf-8")
