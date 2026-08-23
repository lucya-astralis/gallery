"""Terminal presentation layer for the debug CLI (app/debug.py).

Same editorial line as the site itself: a megacorp-terminal HUD — uppercase
labels, thin rules, cyan accents, numbers that line up. Everything here is
about *how* things are printed; what to print lives in debug.py.

Colour is opt-out and self-disabling: no ANSI when the output is piped into a
file, when NO_COLOR is set, when the terminal says it is dumb, or when the
caller asks for --json. Windows consoles get VT processing switched on first,
otherwise they would print the escape codes verbatim.
"""

import os
import shutil
import sys

# Rules and meters are drawn to the real terminal width, clamped so they stay
# readable in a 40-column pane and do not sprawl across an ultrawide one.
MIN_WIDTH = 40
MAX_WIDTH = 96

_color = False


# ----- colour -----------------------------------------------------------
class _Palette:
    """Attribute access returns an escape code, or "" when colour is off, so
    call sites read the same either way: f"{C.cy}TEXT{C.off}"."""

    CODES = {
        "off": "\033[0m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "cy": "\033[36m",
        "cyb": "\033[96m",
        "mg": "\033[35m",
        "ye": "\033[33m",
        "gn": "\033[32m",
        "rd": "\033[31m",
        "gy": "\033[90m",
    }

    def __getattr__(self, name: str) -> str:
        if name not in self.CODES:
            raise AttributeError(name)
        return self.CODES[name] if _color else ""


C = _Palette()


def _enable_windows_vt() -> bool:
    """Turn on ANSI escape handling for the current console (Win10+)."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # -11 = STD_OUTPUT_HANDLE, 0x4 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x4))
    except Exception:
        return False


def init_color(enabled: bool | None = None) -> bool:
    """Decide once whether this run prints colour. `enabled=False` forces it
    off (--no-color, --json); None auto-detects."""
    global _color
    if enabled is False:
        _color = False
        return _color
    if os.environ.get("FORCE_COLOR"):
        # The conventional override: colour even into a pipe (CI logs, less -R,
        # and the test harness). VT setup is attempted but not required — on a
        # pipe there is no console mode to set in the first place.
        _enable_windows_vt()
        _color = True
        return _color
    auto = (
        sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )
    _color = bool(auto and _enable_windows_vt())
    return _color


def width() -> int:
    return max(MIN_WIDTH, min(MAX_WIDTH, shutil.get_terminal_size((80, 24)).columns))


def wide_enough_for_logo() -> bool:
    return width() >= 58


def is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


# ----- primitives -------------------------------------------------------
LABEL_W = 12


def out(text: str = "") -> None:
    print(text)


def kv(label: str, value, tint: str = "") -> None:
    """`LABEL       value` — the workhorse line of every report."""
    pad = f"{label.upper():<{LABEL_W}}"
    print(f"{C.gy}{pad}{C.off}{tint}{value}{C.off if tint else ''}")


def rule(title: str = "") -> None:
    w = width()
    if not title:
        print(f"{C.gy}{'─' * w}{C.off}")
        return
    left = f"── {title.upper()} "
    print(f"{C.cy}{left}{C.gy}{'─' * max(0, w - len(left))}{C.off}")


def head(title: str) -> None:
    print()
    rule(title)


def bar(value: float, peak: float, cells: int = 24, tint: str = "") -> str:
    """A HUD-style meter: filled blocks over a dotted track."""
    if peak <= 0:
        return f"{C.gy}{'·' * cells}{C.off}"
    filled = max(1 if value > 0 else 0, round(cells * value / peak))
    filled = min(cells, filled)
    return (f"{tint or C.cy}{'█' * filled}{C.off}"
            f"{C.gy}{'·' * (cells - filled)}{C.off}")


def state(text: str, level: str = "ok") -> str:
    """Colour a state word: ok / warn / bad / idle."""
    tint = {"ok": C.gn, "warn": C.ye, "bad": C.rd, "idle": C.gy}.get(level, "")
    return f"{tint}{text}{C.off}"


# ----- logo -------------------------------------------------------------
# 5-row block letters, 8 columns each -> 56 columns for GALLERY.
_GLYPHS = {
    "G": (" ██████ ", "██      ", "██  ███ ", "██   ██ ", " ██████ "),
    "A": (" █████  ", "██   ██ ", "███████ ", "██   ██ ", "██   ██ "),
    "L": ("██      ", "██      ", "██      ", "██      ", "███████ "),
    "E": ("███████ ", "██      ", "█████   ", "██      ", "███████ "),
    "R": ("██████  ", "██   ██ ", "██████  ", "██   ██ ", "██   ██ "),
    "Y": ("██    ██", " ██  ██ ", "  ████  ", "   ██   ", "   ██   "),
}
_WORD = "GALLERY"


def logo(subtitle: str = "") -> None:
    """The masthead. Falls back to a single line when the terminal is too
    narrow for the block letters."""
    print()
    if wide_enough_for_logo():
        for row in range(5):
            line = "".join(_GLYPHS[ch][row] for ch in _WORD)
            print(f"{C.cyb}{line}{C.off}")
    else:
        print(f"{C.cyb}{C.bold}▚ {_WORD}{C.off}")
    if subtitle:
        print(f"{C.gy}{subtitle}{C.off}")
    print()


# ----- misc -------------------------------------------------------------
def hint(text: str) -> None:
    print(f"{C.gy}{text}{C.off}")


def warn(text: str) -> None:
    print(f"{C.ye}{text}{C.off}")


def error(text: str) -> None:
    print(f"{C.rd}{text}{C.off}", file=sys.stderr)


def columns(pairs, gap: int = 2, key_tint: str = "") -> None:
    """Aligned two-column list: [(key, description), …]."""
    if not pairs:
        return
    kw = max(len(k) for k, _ in pairs)
    for key, desc in pairs:
        print(f"  {key_tint or C.cy}{key:<{kw}}{C.off}{' ' * gap}{desc}")
