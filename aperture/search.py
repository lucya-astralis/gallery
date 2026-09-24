"""The search grammar: free words, plus filters on what a photo was shot with.

One parser for every place a visitor or an operator can search -- the /search
page, `q` on /api/photos and `python -m aperture.cli search` -- so a query
means the same thing wherever it is typed:

    berlin                    album, file name, tag, camera or lens
    "tv tower"                the same, as one phrase
    camera:x100v              lens:"35mm f/2"
    iso:3200   iso:800-3200   iso:1600-
    f:2.8      f:1.4-2        f/2.8   ƒ2.8   (the way the EXIF panel prints it)
    mm:35      mm:24-70
    date:2026  date:2026-08   date:2026-08-15   date:2026-08-01..2026-08-20
    tag:night                 a tag the photo carries (exactly, any case)
    album:japan_2026          that album and every album under it

The words match any of their fields; every filter narrows what they found. A
filter whose value cannot be read is handed back marked as such and ignored,
rather than turning the whole search into "no matches". The facts filtered on
are columns of their own (capture.py), so no query parses EXIF.

Matching is instr() over lower(), not LIKE: `%` and `_` are LIKE wildcards, and
a single "%" typed into the field used to return the whole library. sqlite's
lower() folds ASCII only, which is as far as the SQL half can go.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# the key as typed -> the fact it filters on. Short and long spellings both:
# "f:" is what a photographer types, "aperture:" what anyone else guesses.
KEYS = {
    "camera": "camera", "cam": "camera",
    "lens": "lens",
    "iso": "iso",
    "f": "aperture", "aperture": "aperture",
    "mm": "focal", "focal": "focal",
    "date": "date",
    "tag": "tag",
    "album": "album",
}

# key:value (value optionally quoted) | "a phrase" | a word
_TOKEN = re.compile(r'([A-Za-z]+):("[^"]*"?|\S*)|"([^"]*)"?|(\S+)')
_NUM = r"\d+(?:[.,]\d+)?"
_DATE = r"\d{4}(?:-\d{2}(?:-\d{2})?)?"
# an aperture written the way the EXIF panel prints it, key or no key
_F_WORD = re.compile(rf"(?:f/|ƒ)({_NUM}(?:-{_NUM})?)", re.IGNORECASE)
# half a stop either side of a printed aperture still reads as that aperture
_F_SLACK = 0.05


@dataclass
class Filter:
    fact: str          # camera, lens, iso, aperture, focal, date, tag or album
    key: str           # the key as it was typed, lower-cased
    value: str         # the value without its quotes
    pos: int           # which token of the query it was
    sql: str | None = None     # None: the value could not be read
    params: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.sql is not None

    @property
    def label(self) -> str:
        return term(self.key, self.value)


@dataclass
class Query:
    text: str
    filters: list[Filter]
    tokens: list[str]

    def without(self, f: Filter) -> str:
        """The query as typed, minus one filter."""
        return " ".join(t for i, t in enumerate(self.tokens) if i != f.pos)


def term(key: str, value) -> str:
    """A filter as it would be typed: `iso:400`, `camera:"Fixture Cam"`."""
    s = str(value).replace('"', "").strip()
    return f'{key}:"{s}"' if not s or re.search(r"\s", s) else f"{key}:{s}"


def parse(q: str | None) -> Query:
    words: list[str] = []
    filters: list[Filter] = []
    tokens: list[str] = []
    for m in _TOKEN.finditer(q or ""):
        key, val, phrase, _word = m.groups()
        raw = m.group(0)
        fact = KEYS.get(key.lower()) if key else None
        shorthand = None if key or phrase is not None else _F_WORD.fullmatch(raw)
        if fact or shorthand:
            f = (Filter("aperture", "f", shorthand.group(1), len(tokens)) if shorthand
                 else Filter(fact, key.lower(), val.strip('"').strip(), len(tokens)))
            _compile(f)
            filters.append(f)
        else:
            # a word with a colon that is no key (10:30, a URL) is just a word
            words.append(phrase if phrase is not None else raw)
        tokens.append(raw)
    return Query(" ".join(w for w in words if w).strip(), filters, tokens)


def _number(s: str) -> float:
    return float(s.replace(",", "."))


def _compile(f: Filter) -> None:
    v = f.value.lower().replace(" ", "")
    if not v:
        return
    # A tag is matched whole: "night" is not "midnight". A folder path is
    # the album and everything under it, as the album pages count it.
    if f.fact == "tag":
        f.sql = ("EXISTS (SELECT 1 FROM image_tags ft JOIN tags fg ON fg.id = ft.tag_id "
                 "WHERE ft.image_id = {a}.id AND lower(fg.name) = ?)")
        f.params = [f.value.strip().lower()]
        return
    if f.fact == "album":
        path = f.value.strip().strip("/").lower()
        f.sql = "(lower({a}.album) = ? OR substr(lower({a}.album), 1, ?) = ?)"
        f.params = [path, len(path) + 1, path + "/"]
        return
    col = "{a}." + f.fact
    if f.fact in ("camera", "lens"):
        f.sql = f"instr(lower(coalesce({col}, '')), ?) > 0"
        f.params = [f.value.lower()]
        return
    if f.fact == "date":
        col = "{a}.taken_at"
        if re.fullmatch(_DATE, v):
            f.sql, f.params = f"substr({col}, 1, ?) = ?", [len(v), v]
            return
        m = re.fullmatch(rf"({_DATE})?\.\.({_DATE})?", v)
        if m and (m.group(1) or m.group(2)):
            parts, params = [], []
            if m.group(1):
                parts.append(f"substr({col}, 1, ?) >= ?")
                params += [len(m.group(1)), m.group(1)]
            if m.group(2):
                parts.append(f"substr({col}, 1, ?) <= ?")
                params += [len(m.group(2)), m.group(2)]
            f.sql, f.params = " AND ".join(parts), params
        return
    # the numeric facts, typed the way the pages print them: ƒ2.8, f/2.8, 35mm
    if f.fact == "aperture":
        v = re.sub(r"^(ƒ|f/?)", "", v)
    elif f.fact == "focal":
        v = v.replace("mm", "")
    slack = _F_SLACK if f.fact == "aperture" else 0
    if re.fullmatch(_NUM, v):
        n = _number(v)
        if f.fact == "aperture":
            f.sql, f.params = f"abs({col} - ?) < ?", [n, slack]
        else:
            f.sql, f.params = f"{col} = ?", [round(n)]
        return
    m = re.fullmatch(rf"({_NUM})?-({_NUM})?", v)
    if m and (m.group(1) or m.group(2)):
        parts, params = [], []
        if m.group(1):
            parts.append(f"{col} >= ?")
            params.append(_number(m.group(1)) - slack)
        if m.group(2):
            parts.append(f"{col} <= ?")
            params.append(_number(m.group(2)) + slack)
        f.sql, f.params = " AND ".join(parts), params


def condition(query: Query, alias: str = "i",
              also: tuple[str, list] | None = None) -> tuple[str, list]:
    """(sql, params) selecting the photos a query finds, over `images` as
    `alias`. `also` is one more alternative for the words to match -- the page
    adds the albums a word names by their display name. A query with neither
    words nor a readable filter finds nothing, never everything."""
    a = alias
    parts: list[str] = []
    params: list = []
    if query.text:
        needle = query.text.lower()
        ors = [
            f"instr(lower({a}.album), ?) > 0",
            f"instr(lower({a}.filename), ?) > 0",
            f"instr(lower(coalesce({a}.camera, '')), ?) > 0",
            f"instr(lower(coalesce({a}.lens, '')), ?) > 0",
            f"EXISTS (SELECT 1 FROM image_tags qit JOIN tags qt ON qt.id = qit.tag_id "
            f"WHERE qit.image_id = {a}.id AND instr(lower(qt.name), ?) > 0)",
        ]
        ors_params: list = [needle] * len(ors)
        # a bare date is a date too, not only a string some file name contains
        if re.fullmatch(_DATE, query.text):
            ors.append(f"substr({a}.taken_at, 1, ?) = ?")
            ors_params += [len(query.text), query.text]
        if also:
            ors.append(also[0])
            ors_params += list(also[1])
        parts.append("(" + " OR ".join(ors) + ")")
        params += ors_params
    for f in query.filters:
        if f.ok:
            parts.append("(" + f.sql.format(a=a) + ")")
            params += f.params
    if not parts:
        return "0", []
    return " AND ".join(parts), params
