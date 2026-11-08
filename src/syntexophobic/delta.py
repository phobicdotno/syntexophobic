"""Delta updates for the AppImage: download only the parts of a new version that the old one
does not already have, as zsync does.

A release carries `<AppImage>.blocks` next to the AppImage: the file cut into fixed blocks,
each with a rolling checksum (cheap to compute at every byte offset) and a BLAKE2 hash.
The updater slides over the AppImage it runs from, finds every block it already has, even
when it moved (a changed file in the squashfs shifts everything after it), copies those and
fetches the rest with HTTP range requests. The result must match the release's SHA-256, or
the updater downloads the whole file instead.

Not zsync's own format: zsync verifies blocks with MD4, which current OpenSSL builds no
longer have, and its tools cannot sign in to a private repository anyway."""

from __future__ import annotations

import hashlib
import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from numba import njit

BLOCK = 16 * 1024
MAGIC = "syntexophobic-blocks/1"
CHUNK = 8 << 20                # bytes of the old file scanned at a time


def _strong(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=16).digest()


def _weak_all(x: np.ndarray, block: int) -> np.ndarray:
    """Rolling checksum of every window of `block` bytes in x (len(x) - block + 1 values):
    a = Σ x, b = Σ (block - i)·x[i], both mod 2³², packed in 64 bits."""
    x64 = x.astype(np.uint64)
    c1 = np.concatenate(([np.uint64(0)], np.cumsum(x64, dtype=np.uint64)))
    idx = np.arange(len(x), dtype=np.uint64)
    c2 = np.concatenate(([np.uint64(0)], np.cumsum(x64 * idx, dtype=np.uint64)))
    n = len(x) - block + 1
    a = c1[block:block + n] - c1[:n]
    p = idx[:n]
    # Σ (block - i)·x[p+i] = block·a - Σ (j - p)·x[j] for j in [p, p+block)
    b = np.uint64(block) * a - (c2[block:block + n] - c2[:n] - p * a)
    mask = np.uint64(0xFFFFFFFF)
    return (a & mask) | ((b & mask) << np.uint64(32))


def _weak(data: bytes) -> int:
    return int(_weak_all(np.frombuffer(data, np.uint8), len(data))[0])


@dataclass
class Index:
    size: int
    sha256: str
    block: int
    weak: np.ndarray           # uint64 per block
    strong: list[bytes]

    def to_bytes(self) -> bytes:
        head = json.dumps({"format": MAGIC, "size": self.size, "sha256": self.sha256,
                           "block": self.block, "blocks": len(self.strong)}).encode()
        body = self.weak.astype("<u8").tobytes() + b"".join(self.strong)
        return len(head).to_bytes(4, "little") + head + zlib.compress(body, 6)

    @classmethod
    def from_bytes(cls, raw: bytes) -> Index:
        n = int.from_bytes(raw[:4], "little")
        try:
            head = json.loads(raw[4:4 + n])
            if head.get("format") != MAGIC:
                raise ValueError(f"not a block index ({head.get('format')})")
            body = zlib.decompress(raw[4 + n:])
            count = head["blocks"]
            if len(body) != 24 * count:
                raise ValueError("block index is cut short")
            weak = np.frombuffer(body[:8 * count], "<u8").astype(np.uint64)
            strong = [body[8 * count + 16 * i: 8 * count + 16 * (i + 1)] for i in range(count)]
            return cls(head["size"], head["sha256"], head["block"], weak, strong)
        except (zlib.error, KeyError, TypeError, AttributeError) as e:   # the caller falls back on ValueError
            raise ValueError(f"broken block index: {e}") from e


def index(path: Path, block: int = BLOCK) -> Index:
    """The block index of a file (the last block padded with zeros)."""
    weak, strong, sha = [], [], hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while data := f.read(block):
            sha.update(data)
            size += len(data)
            data = data.ljust(block, b"\0")
            weak.append(_weak(data))
            strong.append(_strong(data))
    return Index(size, sha.hexdigest(), block, np.array(weak, dtype=np.uint64), strong)


def write_index(path: Path) -> Path:
    out = path.with_name(path.name + ".blocks")
    out.write_bytes(index(path).to_bytes())
    return out


@njit(cache=True, nogil=True)
def _scan(x, block, wanted, seen, sieve):
    """Positions p in x whose rolling checksum of x[p:p+block] is in `wanted` (sorted) and not
    yet `seen` (the first place each turns up: a run of zeros matches at every byte), and
    those checksums: one O(1) update per byte (a -= out, a += in; b -= block·out, b += a)."""
    n = len(x) - block + 1
    pos = np.empty(0, np.int64)
    val = np.empty(0, np.uint64)
    if n <= 0:
        return pos, val
    hits_p, hits_v, k = np.empty(1024, np.int64), np.empty(1024, np.uint64), 0
    M = np.int64(0xFFFFFFFF)
    a, b = np.int64(0), np.int64(0)
    for i in range(block):
        a += x[i]
        b += (block - i) * np.int64(x[i])
    a &= M
    b &= M
    for p in range(n):
        w = np.uint64(a) | (np.uint64(b) << np.uint64(32))
        if sieve[(w * np.uint64(0x9E3779B97F4A7C15)) >> np.uint64(44)]:   # most offsets stop here
            j = np.searchsorted(wanted, w)
            if j < len(wanted) and wanted[j] == w and not seen[j]:
                seen[j] = True
                if k == len(hits_p):
                    hits_p = np.concatenate((hits_p, np.empty(k, np.int64)))
                    hits_v = np.concatenate((hits_v, np.empty(k, np.uint64)))
                hits_p[k] = p
                hits_v[k] = w
                k += 1
        if p + block < len(x):
            out, inn = np.int64(x[p]), np.int64(x[p + block])
            a = (a - out + inn) & M
            b = (b - block * out + a) & M
    return hits_p[:k], hits_v[:k]


def find(old: Path, idx: Index, progress: Callable[[float], None] | None = None) -> list[int | None]:
    """For every block of the new file, an offset in the old file holding the same bytes, or
    None when it has to be downloaded."""
    B = idx.block
    found: list[int | None] = [None] * len(idx.strong)
    by_weak: dict[int, list[int]] = {}
    for i, w in enumerate(idx.weak.tolist()):
        by_weak.setdefault(w, []).append(i)
    wanted = np.array(sorted(by_weak), dtype=np.uint64)
    total = old.stat().st_size
    data = np.memmap(old, np.uint8, "r") if total else np.zeros(0, np.uint8)
    seen = np.zeros(len(wanted), np.bool_)
    sieve = np.zeros(1 << 20, np.bool_)
    sieve[(wanted * np.uint64(0x9E3779B97F4A7C15)) >> np.uint64(44)] = True
    start = 0
    while start + B <= total:
        end = min(start + CHUNK + B - 1, total)
        pos, val = _scan(np.asarray(data[start:end]), B, wanted, seen, sieve)
        for h, value in zip(pos.tolist(), val.tolist()):
            todo = [i for i in by_weak.get(value, ()) if found[i] is None]
            if not todo:
                continue
            off = start + h
            s = _strong(bytes(data[off:off + B]))
            for i in todo:
                if idx.strong[i] == s:
                    found[i] = off
        start = end - B + 1
        if progress:
            progress(min(start / total, 1.0))
    return found


def ranges(found: list[int | None], block: int, size: int, gap: int = 4) -> list[tuple[int, int]]:
    """Byte ranges [start, end) of the new file to download: missing blocks, with runs closer
    than `gap` blocks merged (fewer requests for a few blocks more)."""
    out: list[list[int]] = []
    for i, off in enumerate(found):
        if off is not None:
            continue
        if out and i * block - out[-1][1] <= gap * block:
            out[-1][1] = min((i + 1) * block, size)
        else:
            out.append([i * block, min((i + 1) * block, size)])
    return [(a, b) for a, b in out]


def assemble(old: Path, idx: Index, found: list[int | None], parts: dict[int, bytes], dest: Path):
    """Writes the new file from old blocks and downloaded ranges ({start: bytes}), then checks
    it against the index's SHA-256 (ValueError when it does not match)."""
    B = idx.block
    spans = sorted(parts.items())
    sha = hashlib.sha256()
    with open(old, "rb") as src, open(dest, "wb") as out:
        k = 0
        for i, off in enumerate(found):
            lo, hi = i * B, min((i + 1) * B, idx.size)
            if off is not None:
                src.seek(off)
                data = src.read(B)[:hi - lo]
            else:
                while k < len(spans) and spans[k][0] + len(spans[k][1]) <= lo:
                    k += 1
                start, chunk = spans[k]
                data = chunk[lo - start:hi - start]
            if len(data) != hi - lo:
                raise ValueError(f"block {i} is incomplete")
            out.write(data)
            sha.update(data)
    if sha.hexdigest() != idx.sha256:
        raise ValueError("the rebuilt file does not match the release")


if __name__ == "__main__":     # python -m syntexophobic.delta <AppImage>: writes <AppImage>.blocks
    import sys
    for p in sys.argv[1:]:
        print(write_index(Path(p)))
