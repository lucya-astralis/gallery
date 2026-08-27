"""The photo tree as this tool sees it.

Read straight off the filesystem -- no database, no gallery import. That keeps
the configurator usable against a photos/ share the gallery isn't currently
running on, and means a folder added a second ago is already editable here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import schema

# Metadata folders and the junk file managers leave behind.
_SKIP_DIRS = {schema.ALBUM_META_DIR, "@eaDir", "#recycle", "__pycache__"}
_SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def is_image(name: str) -> bool:
    return Path(name).suffix.lower() in schema.IMAGE_EXTS


def _visible_dir(name: str) -> bool:
    return name not in _SKIP_DIRS and not name.startswith(".")


@dataclass
class Node:
    """One album folder: a path that can carry an album.cfg."""
    path: str                       # relative to the photos root, posix
    name: str
    children: list["Node"] = field(default_factory=list)
    own_photos: int = 0             # images directly in this folder
    total_photos: int = 0           # own + whole subtree
    has_cfg: bool = False
    has_meta: bool = False

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "own_photos": self.own_photos,
            "total_photos": self.total_photos,
            "has_cfg": self.has_cfg,
            "has_meta": self.has_meta,
            "children": [c.as_dict() for c in self.children],
        }


class Library:
    def __init__(self, root: Path):
        self.root = root

    # ----- paths ---------------------------------------------------------
    def safe(self, rel: str) -> Path:
        """Resolve a client-supplied relative path inside the photos root.

        Everything reaching the filesystem goes through here: the tool writes
        into a mounted photo share, so a `../` in a request must never escape.
        """
        rel = (rel or "").replace("\\", "/").strip().strip("/")
        target = (self.root / rel).resolve() if rel else self.root.resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            raise ValueError("path escapes the photos root: %r" % rel)
        return target

    def meta_dir(self, album: str) -> Path:
        return self.safe(album) / schema.ALBUM_META_DIR

    def cfg_path(self, album: str) -> Path:
        return self.meta_dir(album) / schema.ALBUM_CFG_NAME

    def gallery_cfg_path(self) -> Path:
        return self.root / schema.GALLERY_CFG_NAME

    def desc_path(self, album: str, lang: str) -> Path:
        if lang not in schema.LANGS:
            raise ValueError("unknown language: %r" % lang)
        return self.meta_dir(album) / ("album_%s.md" % lang)

    # ----- tree ----------------------------------------------------------
    def tree(self) -> Node:
        return self._scan(self.root, "", self.root.name or "photos")

    def _scan(self, folder: Path, rel: str, name: str) -> Node:
        node = Node(path=rel, name=name)
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            return node
        for entry in entries:
            if entry.is_dir():
                if not _visible_dir(entry.name):
                    continue
                child_rel = ("%s/%s" % (rel, entry.name)) if rel else entry.name
                node.children.append(
                    self._scan(Path(entry.path), child_rel, entry.name))
            elif entry.is_file() and is_image(entry.name):
                node.own_photos += 1
        node.total_photos = node.own_photos + sum(c.total_photos for c in node.children)
        meta = folder / schema.ALBUM_META_DIR
        node.has_meta = meta.is_dir()
        node.has_cfg = (meta / schema.ALBUM_CFG_NAME).is_file()
        return node

    def album_paths(self) -> list[str]:
        """Every album path in the tree, root excluded, depth-first."""
        out: list[str] = []

        def walk(node: Node) -> None:
            for child in node.children:
                out.append(child.path)
                walk(child)

        walk(self.tree())
        return out

    # ----- photos --------------------------------------------------------
    def photos(self, album: str = "", recursive: bool = False) -> list[dict]:
        """Images in an album as {rel, name, sub, size, mtime}.

        `rel` is relative to the photos root (what gallery.cfg welcome entries
        use); `sub` is relative to the album (what album.cfg cover / featured /
        order use). Empty `album` plus `recursive` walks the whole gallery.
        """
        base = self.safe(album)
        if not base.is_dir():
            return []
        out: list[dict] = []
        prefix = (album.strip("/") + "/") if album.strip("/") else ""
        for folder, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted((d for d in dirnames if _visible_dir(d)),
                                 key=str.lower)
            for filename in sorted(filenames, key=str.lower):
                if filename in _SKIP_FILES or not is_image(filename):
                    continue
                full = Path(folder) / filename
                sub = full.relative_to(base).as_posix()
                try:
                    st = full.stat()
                except OSError:
                    continue
                out.append({
                    "rel": prefix + sub,
                    "sub": sub,
                    "name": filename,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })
            if not recursive:
                dirnames[:] = []
        return out

    def resolve_photo(self, album: str, item: str) -> str | None:
        """Resolve one album.cfg photo reference to a path relative to the
        album, or None. Mirrors the gallery's `_resolve_photo_refs`: an exact
        relative path wins, otherwise a case-insensitive match on a bare
        filename anywhere in the subtree, or on any path-suffix."""
        item = (item or "").replace("\\", "/").strip().strip("/")
        if not item:
            return None
        base = self.safe(album)
        # The gallery tolerates a ref that already carries the album prefix.
        prefix = album.strip("/") + "/"
        if album.strip("/") and item.lower().startswith(prefix.lower()):
            item = item[len(prefix):]
        direct = base / item
        if direct.is_file() and is_image(direct.name):
            try:
                return direct.resolve().relative_to(base.resolve()).as_posix()
            except ValueError:
                return None
        key = schema.order_key(item)
        for photo in self.photos(album, recursive=True):
            sub = schema.order_key(photo["sub"])
            if "/" in key:
                if sub == key or sub.endswith("/" + key):
                    return photo["sub"]
            elif schema.order_key(photo["name"]) == key:
                return photo["sub"]
        return None

    def resolve_gallery_photo(self, raw: str) -> str | None:
        """Resolve a gallery.cfg welcome entry (a path relative to the photos
        root) to that path, or None."""
        rel = (raw or "").replace("\\", "/").strip().strip("/")
        if not rel or "/" not in rel:
            return None
        try:
            target = self.safe(rel)
        except ValueError:
            return None
        if target.is_file() and is_image(target.name):
            return rel
        return None

    # ----- .album assets --------------------------------------------------
    def assets(self, album: str) -> list[dict]:
        """Files sitting in the album's .album/ folder, split by what the
        gallery can use them for."""
        meta = self.meta_dir(album)
        if not meta.is_dir():
            return []
        out: list[dict] = []
        for entry in sorted(os.scandir(meta), key=lambda e: e.name.lower()):
            if not entry.is_file() or entry.name in _SKIP_FILES:
                continue
            ext = Path(entry.name).suffix.lower()
            if ext in schema.ICON_EXTS:
                kind = "icon"
            elif ext in schema.FONT_EXTS:
                kind = "font"
            elif entry.name == schema.ALBUM_CFG_NAME:
                kind = "cfg"
            elif entry.name.startswith("album_") and ext == ".md":
                kind = "description"
            else:
                kind = "other"
            out.append({
                "name": entry.name,
                "kind": kind,
                "size": entry.stat().st_size,
            })
        return out

    def read_desc(self, album: str, lang: str) -> str:
        path = self.desc_path(album, lang)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def write_desc(self, album: str, lang: str, text: str) -> None:
        path = self.desc_path(album, lang)
        if not text.strip():
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
