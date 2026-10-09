"""Feeds and speeds starting points for hobby routers (Shapeoko / Nomad class).

Feed = rpm × flutes × chipload. Chiploads are given for a 6 mm cutter and scaled with
diameter (smaller cutters need a thinner chip); depth of cut and stepover are fractions
of the diameter. The numbers are deliberately on the cautious side of the published
router charts: they are where to start, then listen to the cut and adjust.
"""

from __future__ import annotations

from dataclasses import dataclass

from .job import Machine, Operation, Tool


@dataclass(frozen=True)
class Material:
    label: str
    chipload_6mm: float    # mm per tooth for a 6 mm cutter
    doc: float             # roughing depth of cut, × diameter
    stepover: float        # roughing stepover, × diameter
    rpm: int


MATERIALS = {
    "softwood": Material("Softwood (pine, spruce)", 0.060, 0.30, 0.45, 18000),
    "hardwood": Material("Hardwood (oak, maple, walnut)", 0.045, 0.20, 0.40, 18000),
    "mdf": Material("MDF", 0.060, 0.30, 0.45, 18000),
    "plywood": Material("Plywood / Baltic birch", 0.050, 0.25, 0.40, 18000),
    "acrylic": Material("Acrylic (cast)", 0.050, 0.15, 0.40, 16000),
    "hdpe": Material("HDPE / plastics", 0.080, 0.30, 0.45, 16000),
    "aluminium": Material("Aluminium 6061", 0.025, 0.05, 0.30, 18000),
    "brass": Material("Brass", 0.020, 0.05, 0.30, 16000),
}

MAX_FEED = 5000.0     # mm/min; above what a belt-drive router should be asked for


def chipload(material: Material, diameter: float) -> float:
    """Thinner chips for smaller cutters, a little thicker for bigger ones (capped)."""
    scale = max(0.35, min(1.5, diameter / 6.0))
    return material.chipload_6mm * scale


def suggest(material_key: str, tool: Tool, op: Operation, machine: Machine) -> dict:
    """Values to apply to an operation (and the machine rpm), keyed by field name."""
    m = MATERIALS[material_key]
    rpm = machine.rpm or m.rpm
    feed = min(MAX_FEED, rpm * max(tool.flutes, 1) * chipload(m, tool.diameter))
    out = {"feed": round(feed, -1), "plunge": round(feed / 3, -1)}
    if op.type == "adaptive":
        # light radial cut, deep axial: thin chips, so feed up by the chip-thinning factor
        ae = 0.10 if m.doc < 0.1 else 0.15
        thinning = 1 / (1 - (1 - 2 * ae) ** 2) ** 0.5
        afeed = min(MAX_FEED, feed * thinning)
        depth = tool.diameter * (0.5 if m.doc < 0.1 else 1.0)
        out = {"feed": round(afeed, -1), "plunge": round(feed / 3, -1),
               "stepdown": round(depth, 2), "stepover": ae}
    elif op.type == "rough":
        out |= {"stepdown": round(tool.diameter * m.doc, 2), "stepover": m.stepover}
    elif op.type in ("finish", "profile"):
        out |= {"stepdown": round(tool.diameter * m.doc * 2, 2), "feed": round(feed * 0.8, -1)}
    elif op.type == "bore":
        out |= {"stepover": m.stepover}
    elif op.type == "drill":
        # drills feed along the axis: about half the tool's chipload per revolution, 2 lips
        out = {"plunge": round(min(MAX_FEED / 4, rpm * 2 * chipload(m, tool.diameter) * 0.5), -1),
               "stepdown": round(max(tool.diameter, 1.0), 2)}
    return out


def apply(job, op: Operation) -> dict:
    """Apply the suggestion for the job's material to `op`; returns what changed."""
    values = suggest(job.stock.material, job.tool(op.tool), op, job.machine)
    for k, v in values.items():
        setattr(op, k, v)
    return values
