"""Build small OLE compound files (MS-CFB v3) for tests: Outlook .msg messages, and a
non-Outlook OLE file (an old Word document) to prove it is refused.

Only what a reader needs: 512-byte sectors, a FAT, a mini stream for streams under 4096
bytes, and a directory whose siblings are chained to the right (a valid, if lopsided, tree).
"""

import struct

SECTOR, MINI, CUTOFF = 512, 64, 4096
FREE, END, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF


def _key(name: str) -> tuple[int, str]:
    return (len(name), name.upper())  # CFB sibling order: length, then case-insensitive name


def build_ole(tree: dict) -> bytes:
    """tree: {name: bytes (stream) | dict (storage)}; returns the compound file."""
    entries: list[dict] = [{"name": "Root Entry", "type": 5, "children": tree}]

    def walk(idx: int) -> None:
        kids = entries[idx]["children"]
        ids = []
        for name in sorted(kids, key=_key):
            val = kids[name]
            entries.append(
                {
                    "name": name,
                    "type": 1 if isinstance(val, dict) else 2,
                    "children": val if isinstance(val, dict) else None,
                    "data": None if isinstance(val, dict) else val,
                }
            )
            ids.append(len(entries) - 1)
        entries[idx]["child"] = ids[0] if ids else NOSTREAM
        for a, b in zip(ids, ids[1:] + [NOSTREAM], strict=True):
            entries[a]["right"] = b
        for i in ids:
            if entries[i]["type"] == 1:
                walk(i)

    walk(0)

    # mini stream (small streams) and big streams
    mini = bytearray()
    minifat: list[int] = []
    big: list[tuple[int, bytes]] = []
    for i, e in enumerate(entries):
        data = e.get("data")
        if data is None or len(data) == 0:
            e["start"], e["size"] = (END if e["type"] != 1 else 0), 0
            continue
        e["size"] = len(data)
        if len(data) < CUTOFF:
            n = -(-len(data) // MINI)
            e["start"] = len(minifat)
            minifat.extend(range(len(minifat) + 1, len(minifat) + n))
            minifat.append(END)
            mini += data + b"\0" * (n * MINI - len(data))
        else:
            big.append((i, data))

    def sectors_for(n_bytes: int) -> int:
        return -(-n_bytes // SECTOR)

    dir_bytes = -(-len(entries) // 4) * SECTOR
    n_dir = dir_bytes // SECTOR
    n_minifat = sectors_for(len(minifat) * 4)
    n_mini = sectors_for(len(mini))
    n_big = [sectors_for(len(d)) for _, d in big]
    body = n_dir + n_minifat + n_mini + sum(n_big)
    n_fat = 1
    while n_fat * (SECTOR // 4) < body + n_fat:
        n_fat += 1
    assert n_fat <= 109

    fat: list[int] = [FATSECT] * n_fat
    pos = n_fat

    def chain(count: int) -> int:
        nonlocal pos
        if count == 0:
            return END
        start = pos
        fat.extend(list(range(pos + 1, pos + count)) + [END])
        pos += count
        return start

    first_dir = chain(n_dir)
    first_minifat = chain(n_minifat)
    first_mini = chain(n_mini)
    for (i, _), n in zip(big, n_big, strict=True):
        entries[i]["start"] = chain(n)
    entries[0]["start"], entries[0]["size"] = first_mini, n_mini * SECTOR if n_mini else 0
    if not n_mini:
        entries[0]["start"] = END
    fat += [FREE] * (n_fat * (SECTOR // 4) - len(fat))

    def dir_entry(e: dict | None) -> bytes:
        if e is None:
            return (
                b"\0" * 64
                + struct.pack("<HBBIII", 0, 0, 0, NOSTREAM, NOSTREAM, NOSTREAM)
                + b"\0" * 36
                + struct.pack("<IQ", 0, 0)
            )
        name = e["name"].encode("utf-16-le") + b"\0\0"
        return (
            name.ljust(64, b"\0")
            + struct.pack("<HBB", len(name), e["type"], 1)
            + struct.pack("<III", NOSTREAM, e.get("right", NOSTREAM), e.get("child", NOSTREAM))
            + b"\0" * 16
            + b"\0" * 4
            + b"\0" * 16
            + struct.pack("<IQ", e["start"], e["size"])
        )

    directory = b"".join(dir_entry(e) for e in entries)
    directory += b"".join(dir_entry(None) for _ in range(n_dir * 4 - len(entries)))

    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\0" * 16
        + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
        + b"\0" * 6
        + struct.pack(
            "<IIIIIIIII",
            0,
            n_fat,
            first_dir,
            0,
            CUTOFF,
            first_minifat if n_minifat else END,
            n_minifat,
            END,
            0,
        )
        + b"".join(struct.pack("<I", i) for i in range(n_fat))
        + struct.pack("<I", FREE) * (109 - n_fat)
    )
    assert len(header) == SECTOR

    def pad(b: bytes, n: int) -> bytes:
        return b + b"\0" * (n * SECTOR - len(b))

    out = bytearray(header)
    out += b"".join(struct.pack("<I", x) for x in fat)
    out += directory
    out += pad(b"".join(struct.pack("<I", x) for x in minifat), n_minifat)
    out += pad(bytes(mini), n_mini)
    for (_, d), n in zip(big, n_big, strict=True):
        out += pad(d, n)
    return bytes(out)


def _u(s: str) -> bytes:
    return s.encode("utf-16-le")


def outlook_msg(
    subject: str,
    sender_name: str,
    sender_email: str,
    body: str = "",
    html: str | None = None,
    attachments: list[dict] | None = None,
    date: str = "Mon, 16 Nov 2026 10:15:00 +0530",
) -> bytes:
    """attachments: [{name, data, cid?, embedded_msg?}]"""
    tree: dict = {
        "__properties_version1.0": b"\0" * 32,
        "__substg1.0_0037001F": _u(subject),
        "__substg1.0_0C1A001F": _u(sender_name),
        "__substg1.0_5D01001F": _u(sender_email),
        "__substg1.0_007D001F": _u(
            f"From: {sender_name} <{sender_email}>\r\nDate: {date}\r\n"
            f"Subject: {subject}\r\n\r\n"
        ),
    }
    if body:
        tree["__substg1.0_1000001F"] = _u(body)
    if html is not None:
        tree["__substg1.0_10130102"] = html.encode("utf-8")
    for i, a in enumerate(attachments or []):
        st: dict = {"__properties_version1.0": b"\0" * 8, "__substg1.0_3707001F": _u(a["name"])}
        if a.get("embedded_msg"):
            st["__substg1.0_3701000D"] = {"__properties_version1.0": b"\0" * 24}
        else:
            st["__substg1.0_37010102"] = a["data"]
        if a.get("cid"):
            st["__substg1.0_3712001F"] = _u(a["cid"])
        tree[f"__attach_version1.0_#{i:08X}"] = st
    return build_ole(tree)


def word_doc() -> bytes:
    """An OLE file that is not an Outlook message (old Word .doc layout)."""
    return build_ole({"WordDocument": b"\xec\xa5" + b"\0" * 600, "1Table": b"\0" * 300})
