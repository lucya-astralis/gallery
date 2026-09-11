"""The one function that turns a request into a writable path.

Before Aperture the gallery and the configurator each had their own guard —
`main._safe_rel` for the routes that hand out a photograph, `Library.safe` for
the ones that edit a cfg file. Two implementations of "stay inside the photo
tree", one of them on a *writing* path.

This module owns the writing half. `Library.safe` is still what resolves a
folder for reading; what changed is that nothing writes through it any more.
(The reading guard joins this file when main.py is split; the two then share
their normalization and their test file.)

The rule it enforces is narrower than "no traversal", and that is the point.
The console may write in exactly three kinds of place:

    <album>/.album/<name>       an album's own metadata      writable_target()
    .gallery/<name>             the gallery's own metadata   writable_target()
    <album>/<photo>.<ext>.tags  one photo's tag sidecar      sidecar_target()

Nowhere else. Not a photograph, not the root of the share, not a folder that
merely sits next to one. So a target that these two functions return has
already established that the caller cannot overwrite a picture — the guarantee
the read-only mount used to give, expressed as code instead of as a mount
option.

The sidecar is the exception that proves the rule: it is metadata, but the
convention the gallery's scanner reads puts it BESIDE the photo rather than in
a metadata folder. It is therefore tied to the photo — a sidecar is only
writable where an actual image file of that exact name already exists, which
is a tighter condition than any path rule, and the name is derived rather than
accepted.

Six ways a path is refused, each of which has been a real bug in some other
program:

  traversal      `..` in any position, or an absolute path
  the meta rule  the parent must BE `.album`/`.gallery`, and nothing else in
                 the path may be a dotfile — so `.album/.ssh/authorized_keys`
                 is not a metadata file with a nested folder, it is refused
  bare name      the filename is one path segment with no separators
  extension      only what the caller declares it accepts
  windows        reserved device names (CON, NUL, …), alternate data streams
                 (`icon.png:evil`), trailing dots and spaces — all of which a
                 Windows filesystem silently rewrites into something else
  symlinks       no component of the path may be a link, and neither may the
                 target: a link inside `.album/` pointing anywhere else turns
                 a permitted write into an arbitrary one
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from . import schema

ALBUM_META_DIR = schema.ALBUM_META_DIR
GALLERY_META_DIR = schema.GALLERY_META_DIR
META_DIRS = (ALBUM_META_DIR, GALLERY_META_DIR)

# Windows resolves these to devices whatever the extension, and refuses to
# create a file with the name. On a share mounted from Linux they are legal
# filenames, so the check is not the OS's — it is ours, on both.
_RESERVED = {"con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}

# Control characters, the separators, and the three that mean something to a
# Windows path parser: `:` opens an alternate data stream, `*` and `?` glob.
_ILLEGAL = re.compile(r'[\x00-\x1f<>:"|?*\\/]')


class PathRefused(ValueError):
    """A request named a path the console is not allowed to write."""


def _clean_segment(seg: str, what: str) -> str:
    if not seg or seg in (".", ".."):
        raise PathRefused(f"{what} may not be {seg!r}")
    if _ILLEGAL.search(seg):
        raise PathRefused(f"{what} contains a character that is not allowed: {seg!r}")
    # Windows drops a trailing dot or space when it creates the file, so
    # `album.cfg.` and `album.cfg` would be the same file under a name the
    # validator never saw.
    if seg != seg.rstrip(". "):
        raise PathRefused(f"{what} may not end in a dot or a space: {seg!r}")
    if seg.split(".", 1)[0].lower() in _RESERVED:
        raise PathRefused(f"{what} is a reserved device name: {seg!r}")
    return seg


def split_rel(raw: str) -> list[str]:
    """A client-supplied relative path as clean segments, or an exception.

    Accepts either separator, because a Windows client sends backslashes and
    they are a legal filename character on Linux — a path that means one thing
    to the sender and another to the server is where traversal bugs live. The
    backslash is therefore not translated, it is refused (see _ILLEGAL).
    """
    raw = (raw or "").strip()
    if not raw:
        raise PathRefused("empty path")
    if raw.startswith("/") or raw.startswith("\\") or (len(raw) > 1 and raw[1] == ":"):
        raise PathRefused(f"absolute paths are not accepted: {raw!r}")
    parts = [p for p in raw.replace("\\", "/").split("/") if p != ""]
    if not parts:
        raise PathRefused(f"empty path: {raw!r}")
    return [_clean_segment(p, "path segment") for p in parts]


def album_segments(raw: str) -> list[str]:
    """The segments of an album path — no metadata folders among them.

    An album is a folder of photographs. `.album` and `.gallery` are not
    albums, and neither is anything else beginning with a dot, so a request
    that names one is asking for a folder that does not exist in the model.
    """
    parts = split_rel(raw)
    for p in parts:
        if p.startswith("."):
            raise PathRefused(f"{p!r} is a metadata folder, not an album")
    return parts


def writable_target(photos_root: Path, album: str | None, name: str, *,
                    scope: str = "album",
                    allowed_exts: set[str] | None = None) -> Path:
    """The absolute path a console write may land on, or an exception.

    `scope` is "album" (then `album` names it, and may not be empty — the root
    of the share is not an album and a file written there is read by nothing)
    or "gallery" (then `album` is ignored).

    The returned path may or may not exist yet; what is guaranteed is that
    creating it writes inside a metadata folder of this photo tree and nowhere
    else.
    """
    root = Path(photos_root).resolve()

    if scope == "gallery":
        parts = [GALLERY_META_DIR]
    elif scope == "album":
        if not (album or "").strip():
            raise PathRefused(
                "an album is required: the root of the photo share is not an "
                "album, and a metadata file written there is read by nothing")
        parts = album_segments(album) + [ALBUM_META_DIR]
    else:
        raise PathRefused(f"unknown scope: {scope!r}")

    # NOT stripped: a name with a trailing space or dot is refused rather
    # than quietly corrected, because Windows would do the correcting itself
    # and the file would then exist under a name nothing validated.
    leaf = _clean_segment(name or "", "file name")
    if allowed_exts is not None:
        ext = PurePosixPath(leaf).suffix.lower()
        if ext not in allowed_exts:
            raise PathRefused(
                "%s is not a file this folder holds — expected one of %s"
                % (leaf, ", ".join(sorted(allowed_exts))))

    target = root.joinpath(*parts, leaf)

    # Containment, checked on the resolved path rather than on the string: a
    # junction or a mount point below the root would pass a textual check.
    resolved = target.resolve() if target.exists() else _resolve_parent(target)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathRefused(f"path escapes the photo tree: {name!r}")

    _refuse_symlinks(root, target)
    return target


def _resolve_parent(target: Path) -> Path:
    """`resolve()` for a file that does not exist yet: resolve the deepest
    ancestor that does, and re-attach the rest. Python's resolve() is
    non-strict and would happily return a path through a directory that is
    a symlink to somewhere else entirely."""
    existing = target
    tail: list[str] = []
    while not existing.exists():
        tail.append(existing.name)
        parent = existing.parent
        if parent == existing:
            break
        existing = parent
    return existing.resolve().joinpath(*reversed(tail))


def _refuse_symlinks(root: Path, target: Path) -> None:
    """No link anywhere between the root and the file. A single symlink in an
    `.album/` folder is enough to turn every rule above into decoration."""
    if target.is_symlink():
        raise PathRefused(f"{target.name} is a symlink")
    node = target.parent
    while True:
        if node == root:
            return
        if node.is_symlink():
            raise PathRefused(f"{node.name}/ is a symlink")
        parent = node.parent
        if parent == node:                     # walked past the root
            raise PathRefused("path escapes the photo tree")
        node = parent


def sidecar_target(photos_root: Path, photo_rel: str, *,
                   is_image=None) -> Path:
    """The `.tags` file for one photo — `<photo>.<ext>.tags`, beside it.

    The name is DERIVED from the photo's, never taken from the request, so
    there is no filename here for a caller to steer. What the caller supplies
    is which photo, and that has to be one: an existing regular file whose
    extension `is_image` accepts. A path that names a folder, a sidecar, a cfg
    file or nothing at all is refused before any name is built.
    """
    root = Path(photos_root).resolve()
    parts = split_rel(photo_rel)
    for p in parts[:-1]:
        if p.startswith("."):
            raise PathRefused(f"{p!r} is a metadata folder, not an album")
    if parts[-1].startswith("."):
        raise PathRefused("a dotfile is not a photograph")

    photo = root.joinpath(*parts)
    try:
        photo.resolve().relative_to(root)
    except (ValueError, OSError):
        raise PathRefused(f"path escapes the photo tree: {photo_rel!r}")
    _refuse_symlinks(root, photo)

    if not photo.is_file():
        raise PathRefused(f"no such photo: {photo_rel!r}")
    if is_image is not None and not is_image(photo.name):
        raise PathRefused(f"not a photograph: {photo_rel!r}")

    return photo.with_name(photo.name + ".tags")


def relative_to_photos(photos_root: Path, target: Path) -> str:
    """A path as the audit log and the API report it: posix, root-relative."""
    try:
        return target.resolve().relative_to(Path(photos_root).resolve()).as_posix()
    except (ValueError, OSError):
        return os.fspath(target)
