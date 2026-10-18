import dataclasses
import os

import numpy as np
import pytest

from syntexophobic import delta, update

B = 4096


def files(tmp_path):
    rng = np.random.default_rng(1)
    old = rng.integers(0, 256, 300_000, dtype=np.uint8).tobytes() + bytes(40_000)   # a run of zeros too
    new = (old[:50_000] + b"inserted: everything after this moves" + old[50_000:120_000]
           + rng.integers(0, 256, 9_000, dtype=np.uint8).tobytes() + old[130_000:] + b"tail")
    (tmp_path / "old").write_bytes(old)
    (tmp_path / "new").write_bytes(new)
    return tmp_path / "old", tmp_path / "new", new


def test_rolling_checksum_matches_the_index_at_every_offset():
    x = np.random.default_rng(2).integers(0, 256, 3 * B, dtype=np.uint8)
    every = delta._weak_all(x, B)
    wanted = np.sort(every[[0, 7, 1000, 2 * B]])
    sieve = np.zeros(1 << 20, np.bool_)
    sieve[(wanted * np.uint64(0x9E3779B97F4A7C15)) >> np.uint64(44)] = True
    pos, val = delta._scan(x, B, wanted, np.zeros(len(wanted), np.bool_), sieve)
    assert sorted(pos.tolist()) == [0, 7, 1000, 2 * B]
    assert delta._weak(x[7:7 + B].tobytes()) == int(every[7])


def test_only_the_changed_blocks_are_downloaded(tmp_path):
    old, new, data = files(tmp_path)
    idx = delta.Index.from_bytes(delta.index(new, B).to_bytes())     # through the file format
    found = delta.find(old, idx)
    spans = delta.ranges(found, B, idx.size)
    need = sum(b - a for a, b in spans)
    assert need <= 8 * B + 9_000 and len(spans) <= 3               # the insert, the new bytes, the tail
    assert all(off is None or data[i * B:(i + 1) * B] == old.read_bytes()[off:off + B]
               for i, off in enumerate(found[:-1]))
    out = tmp_path / "rebuilt"
    delta.assemble(old, idx, found, {a: data[a:b] for a, b in spans}, out)
    assert out.read_bytes() == data
    bad = {a: bytes(b - a) for a, b in spans}
    with pytest.raises(ValueError, match="does not match"):
        delta.assemble(old, idx, found, bad, out)


def test_install_patches_and_falls_back_to_the_whole_file(tmp_path, monkeypatch):
    old, new, data = files(tmp_path)
    monkeypatch.setattr(update, "LINK", tmp_path / "Syntexophobic.AppImage")
    current = tmp_path / "Syntexophobic-0.6.0-x86_64.AppImage"
    os.replace(old, current)
    (tmp_path / "Syntexophobic.AppImage").symlink_to(current.name)
    blocks = delta.index(new, B).to_bytes()
    release = update.Release("0.7.0", "v0.7.0", "0.7.0", "", "", "Syntexophobic-0.7.0-x86_64.AppImage",
                             "asset", len(data), "blocks")
    asked, whole = [], []

    def get(url, tok, a, b):
        asked.append((url, b - a))
        return data[a:b]

    def fetch(url, dest, tok, size, progress):
        whole.append(url)
        dest.write_bytes(data)

    def patch(rel, cur, dest, tok, progress):
        return update.patch(rel, cur, dest, tok, progress, get=get, read=lambda url, tok: blocks)
    done = update.install(release, current, tok="t", fetch=fetch, patch=patch)
    assert done.path.read_bytes() == data and os.access(done.path, os.X_OK) and not whole
    assert {u for u, _ in asked} == {"asset"} and done.downloaded == sum(n for _, n in asked) + len(blocks)
    assert done.downloaded < len(data) / 4
    assert os.readlink(tmp_path / "Syntexophobic.AppImage") == release.asset

    done.path.unlink()
    broken = lambda rel, cur, dest, tok, progress: update.patch(           # noqa: E731
        rel, cur, dest, tok, progress, get=lambda *a: bytes(a[3] - a[2]), read=lambda url, tok: blocks)
    done = update.install(release, current, tok="t", fetch=fetch, patch=broken)
    assert whole == ["asset"] and done.path.read_bytes() == data and done.downloaded == len(data)
    assert not list(tmp_path.glob("*.part"))

    whole.clear()
    done = update.install(dataclasses.replace(release, blocks_url=""), current, tok="t", fetch=fetch, patch=patch)
    assert whole == ["asset"]                                       # no index: the whole file


def test_release_lists_its_block_index():
    d = {"tag_name": "v1.0.0", "assets": [{"name": "S-1.0.0.AppImage", "url": "a", "size": 3},
                                          {"name": "S-1.0.0.AppImage.blocks", "url": "b", "size": 1}]}
    assert update.releases([d], ".AppImage")[0].blocks_url == "b"


def test_a_broken_index_is_a_value_error(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(os.urandom(100_000))
    raw = delta.index(f, 4096).to_bytes()
    for broken in (raw[:-10], raw[:4] + raw[4:].replace(b'"blocks"', b'"blockz"')):
        with pytest.raises(ValueError):
            delta.Index.from_bytes(broken)
