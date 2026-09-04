"""Terminal presentation layer for the operator CLI (app/cli.py).

Same editorial line as the site itself: a megacorp-terminal HUD — uppercase
labels, thin rules, cyan accents, numbers that line up. Everything here is
about *how* things are printed; what to print lives in cli.py.

Terminal detection is deliberately generous, because `isatty()` lies in two
common setups:

  * **mintty** (Git Bash / MSYS2 / Cygwin on Windows) talks to the process
    through a pipe, so a native Python sees `isatty() == False` even though a
    human is sitting right there. That is the classic "no colours in Git
    Bash" symptom.
  * **stdin redirected but the screen still attached** (`… < file`, some
    `docker exec` invocations, editor terminals). Output is a terminal, input
    is not — the menu can still run by reading `/dev/tty`.

So: colour follows *stdout*, interactivity follows "is there any terminal I
can read from", and both can be forced with `--color` / `--interactive`,
`FORCE_COLOR` / `NO_COLOR`. `python -m app.cli term` prints what was
detected and why.
"""

import base64
import io
import os
import re
import sys
import shutil
from contextlib import contextmanager
from pathlib import Path

# Rules and meters are drawn to the real terminal width, clamped so they stay
# readable in a 40-column pane and do not sprawl across an ultrawide one.
MIN_WIDTH = 40
MAX_WIDTH = 96
LOGO_MIN_WIDTH = 50

_color = False
_force_interactive: bool | None = None
# Text by default: the letterforms are part of the interface. The picture
# protocols stay available for whoever asks for them explicitly — see
# `logo_image` and `--logo`.
_logo_mode = "ascii"
_frame_depth = 0
_real_stdout = None         # the terminal itself while a frame is buffering
_tty_in = None          # a /dev/tty handle, opened lazily
_tty_checked = False


# ----- colour -----------------------------------------------------------
class _Palette:
    """Attribute access returns an escape code, or "" when colour is off, so
    call sites read the same either way: f"{C.cy}TEXT{C.off}"."""

    CODES = {
        "off": "\033[0m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "rev": "\033[7m",
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


# ----- terminal detection -----------------------------------------------
def _msys_pty(stream) -> bool | None:
    """Is this stream an MSYS/Cygwin pseudo-terminal?

    mintty hands the process a named pipe rather than a console, so
    `isatty()` is False and every colour heuristic gives up — the classic
    "no colours in Git Bash" bug. The pipe's NAME gives it away though:
    `\\msys-1888ae32e00d56aa-pty0-to-master`. Asking the handle for it tells
    a real terminal apart from `> out.txt`, which env sniffing cannot.

    Returns None when the question could not be asked at all (not Windows,
    no ctypes, closed handle) — the caller then falls back to the
    environment.
    """
    if os.name != "nt":
        return None
    try:
        fd = stream.fileno()
    except Exception:
        # No OS-level handle behind this stream (a StringIO, a capture
        # wrapper): whatever it is, it is not a terminal.
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt
        import msvcrt

        handle = msvcrt.get_osfhandle(fd)
        if ctypes.windll.kernel32.GetFileType(wt.HANDLE(handle)) != 3:  # FILE_TYPE_PIPE
            return False

        class FILE_NAME_INFO(ctypes.Structure):
            _fields_ = [("FileNameLength", wt.DWORD),
                        ("FileName", ctypes.c_wchar * 1024)]

        info = FILE_NAME_INFO()
        # 2 = FileNameInfo
        ok = ctypes.windll.kernel32.GetFileInformationByHandleEx(
            wt.HANDLE(handle), 2, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            return None
        return _looks_like_pty(info.FileName[:max(0, info.FileNameLength // 2)])
    except Exception:
        return None


def _looks_like_pty(name: str) -> bool:
    r"""Does this pipe name belong to an MSYS/Cygwin pty?

    Real examples:
        \msys-1888ae32e00d56aa-pty0-to-master
        \cygwin-c5e39b7a9d22bafb-pty2-from-master
    """
    return ("-pty" in name) and ("msys-" in name or "cygwin-" in name)


def _mintty_env() -> bool:
    """Last-resort sniff when the handle cannot be inspected."""
    return bool(os.environ.get("MSYSTEM")           # MINGW64, MSYS, …
                or os.environ.get("TERM_PROGRAM") == "mintty"
                or os.environ.get("TERM", "").startswith(("xterm", "screen", "tmux")))


def is_mintty(stream=None) -> bool:
    """Git Bash / MSYS2 / Cygwin on Windows: a real terminal that a native
    Python cannot recognise. Checked per stream, because stdout and stdin can
    be redirected independently."""
    if os.name != "nt":
        return False
    pty = _msys_pty(stream if stream is not None else sys.stdout)
    if pty is not None:
        return pty
    return _mintty_env()


def stdout_is_terminal() -> bool:
    """Is someone looking at this output? Drives colour and cursor tricks."""
    try:
        if sys.stdout.isatty():
            return True
    except (AttributeError, ValueError):
        return False
    return is_mintty(sys.stdout)


def _tty_handle():
    """A readable handle on the controlling terminal, or None.

    Opening /dev/tty is the POSIX way to reach the user even when stdin was
    redirected. It fails exactly when there is no controlling terminal — a
    cron job, a `docker exec -T`, a CI runner — which is precisely when the
    menu must not prompt."""
    global _tty_in, _tty_checked
    if _tty_checked:
        return _tty_in
    _tty_checked = True
    if os.name == "nt":
        return None
    try:
        _tty_in = open("/dev/tty", "r", encoding="utf-8", errors="replace")
    except OSError:
        _tty_in = None
    return _tty_in


def stdin_is_terminal() -> bool:
    try:
        if sys.stdin.isatty():
            return True
    except (AttributeError, ValueError):
        pass
    return is_mintty(sys.stdin)


def interactive() -> bool:
    """May the CLI prompt? True when input and output both reach a human."""
    if _force_interactive is not None:
        return _force_interactive
    if not stdout_is_terminal():
        return False
    return stdin_is_terminal() or _tty_handle() is not None


def force_interactive(value: bool | None) -> None:
    global _force_interactive
    _force_interactive = value


def read_line(prompt: str = "") -> str:
    """Prompt the human. Falls back to the controlling terminal when stdin
    itself was redirected; raises EOFError when there is nothing to read."""
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    if stdin_is_terminal() or _force_interactive:
        line = sys.stdin.readline()
    else:
        handle = _tty_handle()
        if handle is None:
            raise EOFError("no terminal to read from")
        line = handle.readline()
    if line == "":
        raise EOFError
    return line.strip()


def _enable_windows_vt() -> bool:
    """Turn on ANSI escape handling for a Windows console (Win10+). mintty
    needs nothing — it speaks ANSI natively."""
    if os.name != "nt":
        return True
    if is_mintty():
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
    """Decide once whether this run prints colour. True/False force it
    (--color / --no-color / --json); None auto-detects."""
    global _color
    if enabled is False:
        _color = False
        return _color
    if enabled is True or os.environ.get("FORCE_COLOR"):
        _enable_windows_vt()
        _color = True
        return _color
    auto = (
        stdout_is_terminal()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )
    _color = bool(auto and _enable_windows_vt())
    return _color


def term_report() -> dict:
    """Everything the detection above looked at — the answer to "why do I get
    no colours on this box"."""
    return {
        "platform": sys.platform,
        "os.name": os.name,
        "stdout.isatty": bool(getattr(sys.stdout, "isatty", lambda: False)()),
        "stdin.isatty": bool(getattr(sys.stdin, "isatty", lambda: False)()),
        "mintty_out": is_mintty(sys.stdout),
        "mintty_in": is_mintty(sys.stdin),
        "dev_tty": _tty_handle() is not None,
        "TERM": os.environ.get("TERM"),
        "MSYSTEM": os.environ.get("MSYSTEM"),
        "TERM_PROGRAM": os.environ.get("TERM_PROGRAM"),
        "NO_COLOR": os.environ.get("NO_COLOR"),
        "FORCE_COLOR": os.environ.get("FORCE_COLOR"),
        "encoding": getattr(sys.stdout, "encoding", None),
        "columns": shutil.get_terminal_size((80, 24)).columns,
        "COLORTERM": os.environ.get("COLORTERM"),
        "color": _color,
        "interactive": interactive(),
        "ansi": ansi(),
        "images": image_protocol(),
        "logo_mode": _logo_mode,
        "logo_png": str(LOGO_PNG) if LOGO_PNG.is_file() else None,
    }


# ----- geometry / cursor ------------------------------------------------
def width() -> int:
    return max(MIN_WIDTH, min(MAX_WIDTH, shutil.get_terminal_size((80, 24)).columns))


def content_width() -> int:
    """Columns available to content — inside a frame that is less."""
    return width() - (2 + 2 * PAD) if in_frame() else width()


def wide_enough_for_logo() -> bool:
    return content_width() >= LOGO_MIN_WIDTH


def ansi() -> bool:
    """May we move the cursor / repaint? Only on a real screen."""
    return stdout_is_terminal() and os.environ.get("TERM") != "dumb"


def clear_screen() -> None:
    if ansi():
        sys.stdout.write("\033[H\033[2J\033[3J")
        sys.stdout.flush()


def hide_cursor() -> None:
    if ansi():
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()


def show_cursor() -> None:
    if ansi():
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


LABEL_W = 12

# ----- frame ------------------------------------------------------------
# Everything a screen prints goes inside one box, so the CLI reads as a single
# interface rather than a stack of loose reports. Commands keep calling the
# same kv()/head()/columns() helpers; `screen()` captures what they printed
# and draws the frame around it afterwards, which keeps the drawing code in
# exactly one place.
FRAME = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
         "h": "─", "v": "│", "ml": "├", "mr": "┤"}
_SECTION = "\x00"          # marker head()/rule() leave for the framer
_ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")
RESET = "\033[0m"
PAD = 1                     # spaces between the border and the content
WRAP_INDENT = LABEL_W       # continuation lines line up under the value


def visible_len(text: str) -> int:
    """Length as the terminal shows it — escape sequences take no columns."""
    return len(_ANSI_RE.sub("", text))


def _tokens(text: str):
    """Split into (is_escape, chunk) so widths can be measured honestly."""
    out, i = [], 0
    while i < len(text):
        m = _ANSI_RE.match(text, i)
        if m:
            out.append((True, m.group()))
            i = m.end()
        else:
            out.append((False, text[i]))
            i += 1
    return out


def truncate(text: str, limit: int) -> str:
    """Cut to `limit` visible columns, keeping colour sequences intact."""
    if visible_len(text) <= limit:
        return text
    out, seen = [], 0
    for is_escape, chunk in _tokens(text):
        if is_escape:
            out.append(chunk)
            continue
        if seen >= limit - 1:
            break
        out.append(chunk)
        seen += 1
    return "".join(out) + "…" + ("\033[0m" if _color else "")


def wrap(text: str, limit: int, indent: int = 0) -> list[str]:
    """Fold a line to `limit` visible columns, breaking on spaces where it
    can. Continuation lines are indented, so a long value stays under its own
    label instead of being cut off — an ops tool must never hide data."""
    if visible_len(text) <= limit:
        return [text]
    lines: list[str] = []
    current: list[str] = []
    seen = 0
    pad = " " * indent
    break_at = None          # index in `current` just after the last space
    for is_escape, chunk in _tokens(text):
        if is_escape:
            current.append(chunk)
            continue
        if seen >= limit:
            if break_at is not None and break_at > 0:
                head, tail = current[:break_at], current[break_at:]
            else:
                head, tail = current, []
            lines.append("".join(head).rstrip() + (RESET if _color else ""))
            current = [pad] + tail
            seen = indent + sum(1 for c in tail if not c.startswith("\033"))
            break_at = None
        current.append(chunk)
        seen += 1
        if chunk == " ":
            break_at = len(current)
    if current:
        lines.append("".join(current).rstrip())
    return lines


def in_frame() -> bool:
    return _frame_depth > 0


@contextmanager
def screen(title: str = "", right: str = ""):
    """Draw everything printed inside the block as one framed screen."""
    global _frame_depth
    buffer = io.StringIO()
    _frame_depth += 1
    try:
        with _redirect(buffer):
            yield
    finally:
        _frame_depth -= 1
    _draw(title, right, buffer.getvalue().splitlines())


@contextmanager
def _redirect(buffer):
    global _real_stdout
    saved, sys.stdout = sys.stdout, buffer
    _real_stdout = _real_stdout or saved
    try:
        yield
    finally:
        sys.stdout = saved
        if saved is not buffer and _frame_depth <= 1:
            _real_stdout = None


def terminal_stream():
    """Where live output has to go. Inside a frame the normal stdout is a
    buffer that is only printed at the end — a spinner written there would
    arrive as garbage inside the box, long after it was useful."""
    return _real_stdout or sys.stdout


def _band(left: str, right: str, opener: str, closer: str) -> str:
    """A horizontal border with an optional caption on either end."""
    w = width()
    head = f"{opener}{FRAME['h']}"
    if left:
        head += f" {C.cyb}{left.upper()}{C.off}{C.gy} "
    tail = ""
    if right:
        tail = f" {C.cy}{right.upper()}{C.off}{C.gy} {FRAME['h']}"
    fill = max(1, w - visible_len(head) - visible_len(tail) - 1)
    return f"{C.gy}{head}{FRAME['h'] * fill}{tail}{closer}{C.off}"


_GAP_RE = re.compile(r"\S {2,}")


def _continuation_indent(plain: str, inner: int) -> int:
    """Where a folded line should resume.

    Lines in this UI are two columns — `LABEL   value`, `key   description` —
    so a continuation belongs under the second column, not at the left edge.
    Falls back to the line's own indent for prose, and to the label width for
    a line that starts hard left."""
    gap = _GAP_RE.search(plain)
    if gap and gap.end() <= max(8, inner // 2):
        return gap.end()
    lead = len(plain) - len(plain.lstrip(" "))
    return lead or WRAP_INDENT


def _draw(title: str, right: str, lines: list[str]) -> None:
    w = width()
    inner = w - 2 - 2 * PAD
    # A section marker as the very first line would put a divider directly
    # under the top border; the caption belongs in the border itself instead.
    if lines and lines[0].startswith(_SECTION) and not right:
        right = lines[0][len(_SECTION):]
        lines = lines[1:]
    print(_band(title, right, FRAME["tl"], FRAME["tr"]))
    for line in lines:
        if line.startswith(_SECTION):
            print(_band(line[len(_SECTION):], "", FRAME["ml"], FRAME["mr"]))
            continue
        for part in wrap(line, inner, _continuation_indent(_ANSI_RE.sub("", line), inner)):
            body = truncate(part, inner)
            gap = " " * max(0, inner - visible_len(body))
            print(f"{C.gy}{FRAME['v']}{C.off}{' ' * PAD}{body}{gap}{' ' * PAD}"
                  f"{C.gy}{FRAME['v']}{C.off}")
    print(f"{C.gy}{FRAME['bl']}{FRAME['h'] * (w - 2)}{FRAME['br']}{C.off}")


# ----- primitives -------------------------------------------------------


def out(text: str = "") -> None:
    print(text)


def kv(label: str, value, tint: str = "") -> None:
    """`LABEL       value` — the workhorse line of every report."""
    pad = f"{label.upper():<{LABEL_W}}"
    print(f"{C.gy}{pad}{C.off}{tint}{value}{C.off if tint else ''}")


def rule(title: str = "") -> None:
    """A section divider. Inside a screen it becomes part of the frame; the
    marker is picked up by _draw."""
    if in_frame():
        print(_SECTION + title)
        return
    w = width()
    if not title:
        print(f"{C.gy}{'─' * w}{C.off}")
        return
    left = f"── {title.upper()} "
    print(f"{C.cy}{left}{C.gy}{'─' * max(0, w - len(left))}{C.off}")


def head(title: str) -> None:
    """A section: inside a frame the divider replaces the leading blank line,
    outside it keeps the airy layout."""
    if in_frame():
        rule(title)
        return
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


# ----- terminal images --------------------------------------------------
# Terminals can show real pictures, through one of several mutually
# incompatible protocols. Supported here, best first:
#
#   kitty   kitty / ghostty / konsole — PNG over an APC escape, pixel-perfect
#   iterm   iTerm2 / WezTerm — PNG over an OSC 1337 escape, pixel-perfect
#   blocks  anything with 24-bit colour: two pixels per cell as a half-block.
#           Coarse, but it works in Windows Terminal, mintty, VS Code, gnome-
#           terminal … which is most of the places this CLI actually runs.
#
# Sixel is deliberately not auto-detected: telling a sixel terminal apart from
# a non-sixel one needs a DA1 query and a raw-mode read, which is a lot of
# machinery for a masthead. `--logo blocks` covers those terminals.
#
# The PNG is built from the site logo by `python tools/render_logo.py`; when
# it is missing (or nothing can display it) the ASCII masthead below is used.
LOGO_PNG = Path(__file__).parent / "static" / "logo" / "lucya_logo.png"
LOGO_IMAGE_COLS = 32
_KITTY_CHUNK = 4096


def set_logo_mode(mode: str | None) -> None:
    """auto | kitty | iterm | blocks | ascii | off"""
    global _logo_mode
    if mode:
        _logo_mode = mode


def _truecolor() -> bool:
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    if os.environ.get("WT_SESSION") or os.environ.get("KITTY_WINDOW_ID"):
        return True
    if os.environ.get("TERM_PROGRAM"):
        return True
    return "256color" in os.environ.get("TERM", "") or is_mintty()


def image_protocol() -> str:
    """Which picture protocol this terminal can be given: kitty / iterm /
    blocks / none. Env sniffing only — no terminal round-trips, so a
    non-responding terminal can never hang the CLI."""
    if not ansi():
        return "none"          # a pipe or a log file: never emit binary
    term = os.environ.get("TERM", "")
    if os.environ.get("KITTY_WINDOW_ID") or "kitty" in term or "ghostty" in term:
        return "kitty"
    if (os.environ.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm")
            or os.environ.get("LC_TERMINAL") == "iTerm2"):
        return "iterm"
    return "blocks" if _truecolor() else "none"


def _logo_png() -> bytes | None:
    try:
        return LOGO_PNG.read_bytes()
    except OSError:
        return None


def _image_rows(cols: int) -> int:
    """How many text rows a `cols`-wide rendering of the logo occupies. A
    cell is about twice as tall as it is wide, hence the halving."""
    try:
        from PIL import Image

        with Image.open(LOGO_PNG) as img:
            ratio = img.height / img.width
    except Exception:
        ratio = 0.61
    return max(1, round(cols * ratio / 2))


def _emit_kitty(data: bytes, cols: int, rows: int) -> bool:
    """APC _G … ST, base64 PNG in 4k chunks. C=1 keeps the cursor still, so
    the caller controls the layout instead of guessing what the terminal did."""
    payload = base64.standard_b64encode(data).decode("ascii")
    chunks = [payload[i:i + _KITTY_CHUNK] for i in range(0, len(payload), _KITTY_CHUNK)]
    if not chunks:
        return False
    for index, chunk in enumerate(chunks):
        more = 1 if index < len(chunks) - 1 else 0
        if index == 0:
            head = f"a=T,f=100,C=1,c={cols},r={rows},m={more}"
        else:
            head = f"m={more}"
        sys.stdout.write(f"\033_G{head};{chunk}\033\\")
    sys.stdout.write("\n" * rows)
    sys.stdout.flush()
    return True


def _emit_iterm(data: bytes, cols: int) -> bool:
    """OSC 1337 File=… — iTerm2 and WezTerm advance the cursor themselves."""
    payload = base64.standard_b64encode(data).decode("ascii")
    sys.stdout.write(
        f"\033]1337;File=inline=1;size={len(data)};width={cols};"
        f"preserveAspectRatio=1:{payload}\a\n")
    sys.stdout.flush()
    return True


def _emit_blocks(cols: int) -> bool:
    """Two pixels per cell: the upper half-block takes the top pixel as its
    foreground, the bottom pixel becomes the background. Fully transparent
    pixels are left as gaps so the terminal's own background shows through —
    the logo is white-on-nothing and must not arrive in a white box."""
    try:
        from PIL import Image

        with Image.open(LOGO_PNG) as source:
            img = source.convert("RGBA")
            rows_px = max(2, round(cols * img.height / img.width))
            if rows_px % 2:
                rows_px += 1
            img = img.resize((cols, rows_px), Image.LANCZOS)
    except Exception:
        return False
    px = img.load()
    for y in range(0, img.height, 2):
        line = []
        for x in range(img.width):
            tr, tg, tb, ta = px[x, y]
            br, bg_, bb, ba = px[x, y + 1] if y + 1 < img.height else (0, 0, 0, 0)
            top, bottom = ta > 64, ba > 64
            if not top and not bottom:
                line.append("\033[0m ")
            elif top and bottom:
                line.append(f"\033[38;2;{tr};{tg};{tb}m\033[48;2;{br};{bg_};{bb}m▀")
            elif top:
                line.append(f"\033[49m\033[38;2;{tr};{tg};{tb}m▀")
            else:
                line.append(f"\033[49m\033[38;2;{br};{bg_};{bb}m▄")
        print("".join(line) + "\033[0m")
    return True


def logo_image(cols: int | None = None) -> bool:
    """Draw the real logo if this terminal and the chosen mode allow it."""
    if _logo_mode in ("ascii", "off"):
        return False
    protocol = _logo_mode if _logo_mode != "auto" else image_protocol()
    if in_frame() and protocol in ("kitty", "iterm"):
        # those move the cursor themselves, which would tear the frame apart
        return False
    if protocol in ("none", "ascii"):
        return False
    cols = cols or min(LOGO_IMAGE_COLS, max(8, content_width() - 2))
    if protocol == "blocks":
        return _emit_blocks(cols)
    data = _logo_png()
    if data is None:
        return False
    if protocol == "kitty":
        return _emit_kitty(data, cols, _image_rows(cols))
    if protocol == "iterm":
        return _emit_iterm(data, cols)
    return False


# ----- logo -------------------------------------------------------------
LOGO = r"""
  ________       .__  .__
 /  _____/_____  |  | |  |   ___________ ___.__.
/   \  ___\__  \ |  | |  | _/ __ \_  __ <   |  |
\    \_\  \/ __ \|  |_|  |_\  ___/|  | \/\___  |
 \______  (____  /____/____/\___  >__|   / ____|
        \/     \/               \/       \/
"""


def logo(subtitle: str = "") -> None:
    """The masthead. Letterforms by default; a picture only when explicitly
    asked for. Blank lines above and below the wordmark keep it breathing
    inside the frame."""
    print()
    drew = _logo_mode not in ("ascii", "off") and logo_image()
    if not drew and _logo_mode != "off":
        if wide_enough_for_logo():
            for line in LOGO.strip("\n").splitlines():
                print(f"{C.cyb}{line}{C.off}")
        else:
            print(f"{C.cyb}{C.bold}▚ GALLERY{C.off}")
    if subtitle:
        print()
        print(f"{C.gy}{subtitle}{C.off}")
    print()


# ----- live feedback ----------------------------------------------------
_SPIN_UNICODE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPIN_ASCII = "|/-\\"


def _spinner_frames() -> str:
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return _SPIN_UNICODE if "utf" in enc else _SPIN_ASCII


class Live:
    """One line that rewrites itself — spinners, counters, progress.

    On anything that is not a screen it stays quiet and only prints the final
    `done()` line, so piping into a log file does not produce a wall of
    half-drawn frames.
    """

    def __init__(self, label: str = "", tint: str = "", enabled: bool = True):
        self.label = label
        self.tint = tint or C.cy
        self.frames = _spinner_frames()
        self.i = 0
        # `enabled=False` is for --json: a repainting line would be erased
        # again anyway, but never risk a stray frame in machine-read output.
        self.live = ansi() and enabled
        self._painted = False

    def _paint(self, body: str) -> None:
        stream = terminal_stream()
        stream.write("\r\033[2K" + truncate(body, width() - 1))
        stream.flush()
        self._painted = True

    def tick(self, text: str = "") -> None:
        """Advance the spinner. Call it from the loop you are waiting in."""
        if not self.live:
            return
        frame = self.frames[self.i % len(self.frames)]
        self.i += 1
        self._paint(f"{self.tint}{frame}{C.off} {self.label}{(' ' + text) if text else ''}")

    def progress(self, done: int, total: int, text: str = "") -> None:
        """Advance a bounded operation: meter, count and percentage."""
        if not self.live:
            return
        cells = max(8, min(28, width() - 44))
        pct = (done / total * 100) if total else 0
        self._paint(f"{bar(done, total or 1, cells, self.tint)} "
                    f"{C.bold}{done}/{total}{C.off} {pct:4.0f}%"
                    f"{(' ' + C.gy + text + C.off) if text else ''}")

    def done(self, text: str = "") -> None:
        """Wipe the live line; print the final one if there is one."""
        if self.live and self._painted:
            stream = terminal_stream()
            stream.write("\r\033[2K")
            stream.flush()
        if text:
            print(text)
