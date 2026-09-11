"""The cfg format: one parser, one writer, for both surfaces.

`album.cfg` and `gallery.cfg` used to be read by two implementations of the
same grammar -- `main._parse_cfg` in the gallery, `parse` here in the
configurator -- kept in step by a comment saying "mirror of". They were
byte-identical, which is the best case for a copy and still a copy: the next
change to the grammar would have landed in one of them.

This module is now the only one. The gallery binds its `parse`,
`first`, `as_bool` and `joined` to the functions below, and the
console edits files through `CfgFile`.

Reading is what the gallery needs. Writing is line-based, because the shipped
files are heavily commented -- the comments ARE the documentation -- so an
edit touches only the lines of the key being changed and leaves every comment,
blank line and neighbouring key exactly where it was.

What a key MEANS is not in here; that is `aperture/schema.py`.
"""

from __future__ import annotations

from pathlib import Path

# Keys whose values may carry `#label` group markers (gallery.cfg album_order).
GROUP_KEYS = frozenset({"album_order"})

# The two truthiness vocabularies every boolean-ish key is read with. FALSE is
# wider than "not TRUE" on purpose: `reel = hide` and `stats = none` are real
# spellings an operator uses, and a key that treats them as unknown would turn
# a deliberate "off" into a validation error.
TRUE = frozenset({"1", "true", "yes", "on"})
FALSE = frozenset({"0", "false", "no", "off", "none", "hide"})


def parse(text: str, group_keys: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """Parse cfg text.

    Lower-cased key -> [values]. Repeated keys and comma lists accumulate in
    order; a bare line (no `=`) appends to the key above it. A key written with
    an empty value still registers as an empty list, so "present but empty" is
    distinguishable from "absent". Inside a `group_keys` key a bare `#label`
    line (`#` glued to the label) survives as a `"#label"` entry.
    """
    out: dict[str, list[str]] = {}
    key: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line[0] in "#;":
            label = line[1:].strip()
            if (line[0] == "#" and label and key in group_keys
                    and not line[1].isspace() and line[1] not in "#;"):
                out[key].append("#" + label)
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip().lower()
            out.setdefault(key, [])
        elif key is None:
            continue  # stray line before any key
        else:
            val = line
        out[key].extend(i.strip() for i in val.split(",") if i.strip())
    return out


def first(cfg: dict[str, list[str]], key: str) -> str | None:
    """First configured value for a scalar key, or None."""
    vals = cfg.get(key)
    return vals[0] if vals else None


def joined(cfg: dict[str, list[str]], key: str) -> str:
    """A scalar key whose value is PROSE, with the parser's comma split
    undone. Every value is comma-split on the way in, which is what list keys
    want and what a sentence does not: `site_desc = Archive, mostly Japan.`
    arrives as two values, and taking the first would silently truncate it at
    the comma. Rejoining reconstructs the line (whitespace around the commas
    is normalized, which no display string minds)."""
    return ", ".join(cfg.get(key) or []).strip()


def as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUE


class CfgFile:
    """One cfg file held as its raw lines, edited key by key."""

    def __init__(self, text: str = "", group_keys: frozenset[str] = frozenset()):
        self.group_keys = frozenset(group_keys)
        self.lines = text.splitlines()

    # ----- loading / saving ---------------------------------------------
    @classmethod
    def load(cls, path: Path, group_keys: frozenset[str] = frozenset()) -> "CfgFile":
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        return cls(text, group_keys)

    def text(self) -> str:
        body = "\n".join(self.lines)
        return body + "\n" if body else ""

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.text(), encoding="utf-8", newline="\n")

    def replace_text(self, text: str) -> None:
        self.lines = text.splitlines()

    # ----- reading -------------------------------------------------------
    def values(self) -> dict[str, list[str]]:
        return parse(self.text(), self.group_keys)

    def _records(self) -> list[tuple[str, int, list[int]]]:
        """Every key occurrence as (key, index of its `key =` line, indices of
        the lines continuing it). Comment lines are skipped -- they belong to
        whatever the author wrote them for, so edits never move or drop them --
        except a `#label` group marker inside a group key, which is a value."""
        recs: list[tuple[str, int, list[int]]] = []
        cur: tuple[str, int, list[int]] | None = None
        for i, raw in enumerate(self.lines):
            s = raw.strip()
            if not s:
                continue
            if s[0] in "#;":
                label = s[1:].strip()
                if (s[0] == "#" and label and cur and cur[0] in self.group_keys
                        and not s[1].isspace() and s[1] not in "#;"):
                    cur[2].append(i)
                continue
            if "=" in s:
                cur = (s.partition("=")[0].strip().lower(), i, [])
                recs.append(cur)
            elif cur is not None:
                cur[2].append(i)
        return recs

    def has(self, key: str) -> bool:
        return any(r[0] == key.lower() for r in self._records())

    # ----- writing -------------------------------------------------------
    def set(self, key: str, values: list[str], multiline: bool = False,
            indent: str = "    ") -> None:
        """Point `key` at `values`, dropping any earlier definition of it.

        An empty list removes the key entirely. `multiline` writes one entry
        per line below a bare `key =` header -- the form the shipped files use
        for long lists, and the only safe form for a value holding a comma.
        """
        key = key.lower()
        values = [v for v in (str(v).strip() for v in values) if v]
        if not values:
            self.unset(key)
            return
        if multiline:
            block = [key + " ="] + [indent + v for v in values]
        else:
            block = [key + " = " + ", ".join(values)]
        self._replace_block(key, block)

    def set_repeated(self, key: str, values: list[str]) -> None:
        """Write one `key = value` line per entry. Used for `stat`, whose
        values are freeform `Label: Value` pairs that must not be merged into
        a single comma list (the parser would split them apart again)."""
        key = key.lower()
        values = [v for v in (str(v).strip() for v in values) if v]
        if not values:
            self.unset(key)
            return
        self._replace_block(key, [key + " = " + v for v in values])

    def unset(self, key: str) -> None:
        key = key.lower()
        drop: set[int] = set()
        for k, idx, cont in self._records():
            if k == key:
                drop.add(idx)
                drop.update(cont)
        self._rebuild(drop)

    def _rebuild(self, drop: set[int], anchor: int = -1,
                 block: list[str] | None = None) -> None:
        """Drop the given line indices, optionally inserting `block` where
        `anchor` was. The blank line on each side of a removed key would
        otherwise pile up, so a gap left between two blanks collapses to one --
        editing a file repeatedly must not slowly space it out."""
        if not drop and block is None:
            return
        out: list[str] = []
        seam = False  # something was just removed here
        for i, raw in enumerate(self.lines):
            if i == anchor and block is not None:
                out.extend(block)
                seam = False
                continue
            if i in drop:
                seam = True
                continue
            if seam and not raw.strip() and out and not out[-1].strip():
                continue
            seam = False
            out.append(raw)
        self.lines = out

    def _replace_block(self, key: str, block: list[str]) -> None:
        """Swap the key's existing lines for `block`, in place at its first
        occurrence. A key not yet in the file is appended at the end."""
        recs = [r for r in self._records() if r[0] == key]
        if not recs:
            if self.lines and self.lines[-1].strip():
                self.lines.append("")
            self.lines.extend(block)
            return

        anchor = recs[0][1]
        drop: set[int] = set(recs[0][2])
        for _, idx, cont in recs[1:]:
            drop.add(idx)
            drop.update(cont)

        self._rebuild(drop, anchor, block)

    def apply(self, updates: dict[str, list[str] | None], spec: dict) -> None:
        """Apply a {key: values} patch. `None` (or an empty list) removes the
        key; `spec` maps a key to its write style -- see KEY_SPEC in schema."""
        for key, values in updates.items():
            style = spec.get(key, {})
            if values is None:
                self.unset(key)
            elif style.get("repeated"):
                self.set_repeated(key, values)
            else:
                self.set(key, values, multiline=style.get("multiline", False))
