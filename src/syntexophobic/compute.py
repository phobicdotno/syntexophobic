"""Pluggable compute backends for the number-crunching parts (mesh slicing for now).

Every backend exposes an array module with the numpy API, so the same vectorised code
runs on:

- "numpy": CPU, always available.
- "cupy":  NVIDIA CUDA, or AMD ROCm with a ROCm build of CuPy.

Pick one with the SYNTEXOPHOBIC_BACKEND environment variable or `--backend`; "auto"
(the default) uses the first GPU backend that imports and works, else numpy.

To add another GPU stack (Vulkan, Metal, oneAPI...), write a module that provides a
numpy-compatible array namespace and register it in BACKENDS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np


@dataclass
class Backend:
    name: str
    xp: object                  # numpy-compatible array module
    device: str

    def asarray(self, a):
        return self.xp.asarray(a)

    def to_numpy(self, a):
        return a.get() if hasattr(a, "get") else np.asarray(a)


def _numpy() -> Backend:
    return Backend("numpy", np, "CPU")


def _cupy() -> Backend:
    import cupy

    cupy.zeros(1).sum().item()          # fails here if no usable GPU or driver
    props = cupy.cuda.runtime.getDeviceProperties(cupy.cuda.Device().id)
    name = props["name"].decode() if isinstance(props["name"], bytes) else props["name"]
    return Backend("cupy", cupy, name)


BACKENDS = {"numpy": _numpy, "cupy": _cupy}
GPU_ORDER = ["cupy"]

_active: Backend | None = None


def available() -> dict[str, str]:
    """Backend name -> device, or the reason it can't be used."""
    out = {}
    for name, make in BACKENDS.items():
        try:
            out[name] = make().device
        except Exception as e:  # missing package, no driver, no device
            out[name] = f"unavailable ({type(e).__name__}: {str(e).splitlines()[0][:80]})"
    return out


def get(name: str | None = None) -> Backend:
    """The backend to use: by name, by $SYNTEXOPHOBIC_BACKEND, or the best available."""
    global _active
    name = name or os.environ.get("SYNTEXOPHOBIC_BACKEND", "auto")
    if _active is not None and name in ("auto", _active.name):
        return _active
    if name == "auto":
        for gpu in GPU_ORDER:
            try:
                _active = BACKENDS[gpu]()
                return _active
            except Exception:
                continue
        _active = _numpy()
        return _active
    if name not in BACKENDS:
        raise ValueError(f"Unknown backend {name!r}. Choose from: auto, {', '.join(BACKENDS)}")
    _active = BACKENDS[name]()
    return _active


def slice_segments(backend: Backend, tri, z: float) -> np.ndarray:
    """Cut a triangle soup with the plane at height z.

    tri: (n, 3, 3) array on the backend's device. Returns (m, 2, 2) segment endpoints in
    XY as a numpy array. Each crossing edge is interpolated from its lexicographically
    smaller vertex, so the two triangles sharing an edge produce bit-identical points
    and the segments chain exactly.
    """
    xp = backend.xp
    above = tri[:, :, 2] > z
    count = above.sum(axis=1)
    keep = (count == 1) | (count == 2)
    t, ab = tri[keep], above[keep]
    if t.shape[0] == 0:
        return np.empty((0, 2, 2))
    nxt = xp.asarray([1, 2, 0])
    a, b = t, t[:, nxt]
    crosses = ab != ab[:, nxt]                          # (k, 3): exactly two per triangle
    swap = (a[..., 0] > b[..., 0]) | (
        (a[..., 0] == b[..., 0]) & ((a[..., 1] > b[..., 1]) | ((a[..., 1] == b[..., 1]) & (a[..., 2] > b[..., 2]))))
    lo = xp.where(swap[..., None], b, a)
    hi = xp.where(swap[..., None], a, b)
    dz = hi[..., 2] - lo[..., 2]
    k = (z - lo[..., 2]) / xp.where(crosses, dz, 1.0)
    pts = lo[..., :2] + (hi[..., :2] - lo[..., :2]) * k[..., None]   # (k, 3, 2)
    first = xp.argmax(crosses, axis=1)
    second = 2 - xp.argmax(crosses[:, ::-1], axis=1)
    rows = xp.arange(t.shape[0])
    seg = xp.stack([pts[rows, first], pts[rows, second]], axis=1)
    return backend.to_numpy(seg)
