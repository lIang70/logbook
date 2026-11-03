#!/usr/bin/env python3
"""Check the box-drawing / ASCII diagrams inside fenced code blocks.

Why this exists: a monospace renderer lays out with *display width*, where a
CJK character is two columns wide. Padding a diagram by character count looks
right in the editor and is off by one for every CJK string in it. Prettier pads
tables by display width, but it never touches the contents of a ```text fence,
so these diagrams need their own gate.

Rules are deliberately conservative -- branched state machines, tree diagrams
and ragged right edges are all legitimate, so nothing here compares line widths:

  R1  closed box    a border line made only of corners and dashes must be closed
                    by a bottom border at the same two columns, and every line
                    between them must carry a side vertical at both columns.
  R2  band border   a line that draws a band (corners, dashes and labels, with no
                    vertical in between) must agree on every corner column with
                    any other band that starts at the same column.
  R3  tree anchors  if a line places two or more branch markers, the block is
                    laying out columns, so every marker column must be shared by
                    at least two lines.

Reports only; it never rewrites a diagram. Exit 1 when something is found.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", ".venv", ".build", "dist", "out"}

TOP = set("┌┐─┬╔╗═╦")
BOT = set("└┘─┴┬┼┤├│╚╝═╩╠╣")
VERT = set("│║┼┤├┬┴")
CORNER = set("+┌┐└┘╔╗╚╝")
BRANCH = set("├└")
BAND_FILL = set("─-=═►◄→← \t")
LABEL_OK = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "(),./:%'\"_*"
)
DIAGRAM_LANGS = {"", "text", "txt", "ascii", "plain"}


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def columns(line: str, chars) -> list[int]:
    """Display columns (0-based) of every occurrence of `chars`."""
    out, pos = [], 0
    for c in line:
        if c in chars:
            out.append(pos)
        pos += 2 if unicodedata.east_asian_width(c) in "WF" else 1
    return out


def blocks(text: str):
    """Yield (line_number, info_string, lines) for each fenced block."""
    cur: list[str] = []
    info = ""
    start = 0
    inside = False
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not inside:
                inside, info, start, cur = True, stripped[3:].strip(), number + 1, []
            else:
                inside = False
                yield start, info, cur
            continue
        if inside:
            cur.append(line)


def is_pure_border(line: str, chars) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= chars


def is_band(segment: str) -> bool:
    """A border drawn with corners and labels but no vertical in between."""
    if any(c in VERT for c in segment):
        return False
    if not any(c in "─-=" for c in segment):
        return False
    allowed = CORNER | BAND_FILL | LABEL_OK
    return all(
        c in allowed or unicodedata.east_asian_width(c) in "WF" for c in segment
    )


def check_block(lines: list[str], first_line: int):
    findings = []

    # R1: closed boxes drawn with box-drawing characters. The top border must
    # be a pure border line; the bottom is located by its left corner column,
    # which may share a line with connectors (see control_loop's state machine).
    for i, line in enumerate(lines):
        if not (is_pure_border(line, TOP) and line.strip()[0] in "┌╔"):
            continue
        if line.strip()[-1] not in "┐╗":
            continue
        left, right = columns(line, "┌╔")[0], columns(line, "┐╗")[-1]
        bottom = next(
            (
                j
                for j in range(i + 1, len(lines))
                if left in columns(lines[j], "└╚")
            ),
            None,
        )
        if bottom is None:
            findings.append((first_line + i, "box border is never closed"))
            continue
        closing = lines[bottom]
        right_at_bottom = columns(closing, "┘╝")
        if right not in right_at_bottom:
            findings.append(
                (
                    first_line + bottom,
                    f"box bottom closes at column {right_at_bottom}, "
                    f"top closes at {right}",
                )
            )
        for j in range(i + 1, bottom):
            inner = lines[j]
            sides = columns(inner, VERT)
            if left not in sides or right not in sides:
                findings.append(
                    (
                        first_line + j,
                        f"box interior misses a side vertical at column "
                        f"{left} or {right} (found {sides})",
                    )
                )

    # R2: band borders (corners + dashes + labels, no vertical in between).
    bands: dict[tuple[int, int], list[tuple[int, list[int]]]] = {}
    for i, line in enumerate(lines):
        corners = columns(line, CORNER)
        if len(corners) < 2 or not is_band(line.strip()):
            continue
        bands.setdefault((corners[0], len(corners)), []).append((i, corners))

    for (head, _count), group in bands.items():
        if len(group) < 2:
            continue
        reference = group[0][1]
        for i, corners in group[1:]:
            if corners != reference:
                findings.append(
                    (
                        first_line + i,
                        f"band corners {corners} disagree with {reference} "
                        f"on the band starting at column {head}",
                    )
                )

    # R3: multi-column tree layouts must reuse their anchor columns.
    markers = [columns(line, BRANCH) for line in lines]
    if any(len(m) >= 2 for m in markers):
        used: dict[int, int] = {}
        for m in markers:
            for c in m:
                used[c] = used.get(c, 0) + 1
        for c, count in sorted(used.items()):
            if count == 1:
                findings.append(
                    (first_line, f"column layout uses branch column {c} only once")
                )

    return findings


def note_files(paths):
    if paths:
        for p in paths:
            full = Path(p) if Path(p).is_absolute() else Path.cwd() / p
            if full.is_dir():
                yield from sorted(full.rglob("*.md"))
            elif full.suffix == ".md":
                yield full
        return
    for path in sorted(ROOT.rglob("*.md")):
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        yield path


def main(argv):
    args = [a for a in argv if not a.startswith("-")]
    unknown = [a for a in argv if a.startswith("-")]
    if unknown:
        print("unknown option: " + ", ".join(unknown), file=sys.stderr)
        return 2

    files = list(note_files(args))
    problems = diagrams = 0
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for start, info, lines in blocks(text):
            if info.lower() not in DIAGRAM_LANGS:
                continue
            if not any(c in "\n".join(lines) for c in CORNER | set("─│╔═")):
                continue
            diagrams += 1
            for line_no, message in check_block(lines, start):
                problems += 1
                try:
                    rel = path.relative_to(ROOT)
                except ValueError:
                    rel = path
                print(f"{rel}:{line_no}: error: {message}")

    print(f"{diagrams} diagram(s) checked, {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
