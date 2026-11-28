#!/usr/bin/env python3
"""Deterministic read-only validator for Agent Skills, governance docs, and DESIGN.json.

Pure Python 3 standard library (zero third-party dependencies, non-interactive).

Checks:
1. SKILL.md files:
   - UTF-8 BOM portability warning (strict extractors like Codex require '---' on line 1).
   - Documented YAML stdlib-subset frontmatter parsing (mappings, nested mappings, block/flow
     sequences, inline '#' comments outside quotes, single/double quoted strings, folded/literal
     block scalars with chomping & indent indicators, multiline plain scalars, booleans/nulls,
     anchors/aliases, and duplicate-key rejection).
   - Required 'name' (<= 64 chars, ^[a-z0-9]+(-[a-z0-9]+)*$, matches parent directory),
     required 'description' (non-empty, <= 1024 chars), optional 'compatibility' (1-500 chars),
     optional 'metadata' (mapping), standard agentskills.io keys check, and generic < 500 lines
     body budget warning.
2. Codex sidecar agents/openai.yaml (if present):
   - Nested interface/policy YAML validation, interface.short_description length (25-64 chars),
     interface.default_prompt '$<skill-name>' check, and strict unquoted YAML boolean check
     for policy.allow_implicit_invocation.
3. Markdown governance & skill docs (discovered via pruned relative walk plus each SKILL.md's
   skill directory, even in standalone single-skill repos without a 'skills/' parent):
   - Broken relative links after stripping 3+ backtick/tilde fenced code blocks and inline code
     spans; supports inline links with balanced parens and optional titles, CommonMark angle-bracket
     destinations (<path with spaces.md>), URL-encoded paths (%20), and reference-style link
     definitions ([id]: dest).
   - 1-hop reference topology warning when a Markdown file under <skill-dir>/references/
     links relatively to another file under <skill-dir>/references/.
   - Scope note: renderer-specific '#anchors' and leading '/' (or Windows drive/UNC) routes
     (before or after URL decoding) are outside generic relative-link checks and are intentionally
     skipped.
4. DESIGN.json (if present):
   - Strict RFC 8259 JSON syntax (rejecting non-standard NaN, Infinity, -Infinity),
     root object check, and dict type validation for standard token layers
     (primitive, semantic, component).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import re
import sys
import urllib.parse
from typing import Any

STANDARD_SKILL_KEYS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GOVERNANCE_DOC_NAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "PRODUCT.md",
    "DESIGN.md",
    "README.md",
    "SKILL.md",
    ".impeccable.md",
}
SKIP_DIRS = {
    ".git",
    ".serena",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
}
STANDARD_TOKEN_LAYERS = ("primitive", "semantic", "component")
EXTERNAL_SCHEMES = (
    "http://",
    "https://",
    "mailto:",
    "file://",
    "conversation://",
    "ftp://",
    "tel:",
    "data:",
)

BLOCK_SCALAR_HEADER_RE = re.compile(
    r"^(?P<style>[|>])(?P<mod>(?:[+-]?[1-9]?|[1-9][+-]))(?:[ \t]+#.*|[ \t]*)$"
)
FENCE_OPEN_RE = re.compile(r"^(?:[ \t]*>[ \t]?)*[ \t]*(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
FENCE_CLOSE_RE = re.compile(r"^(?:[ \t]*>[ \t]?)*[ \t]*(?P<fence>`{3,}|~{3,})[ \t]*$")
REF_DEF_LINE_RE = re.compile(r"^[ ]{0,3}\[(?P<label>[^\[\]\n]+)\]:[ \t]*(?P<rest>.*)$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
YAML_ANCHOR_NAME_RE = re.compile(r"^[^\s,\[\]{}]+$")
YAML_ANCHOR_PREFIX_RE = re.compile(r"^&([^\s,\[\]{}]+)(?:[ \t]+(.*)|[ \t]*)$")
ASCII_PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"

YAML_TRUE_TOKENS = {"true", "True", "TRUE"}
YAML_FALSE_TOKENS = {"false", "False", "FALSE"}
YAML_NULL_TOKENS = {"null", "Null", "NULL", "~"}

_DOUBLE_QUOTE_ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "\t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\x85",
    "_": "\xa0",
    "L": "\u2028",
    "P": "\u2029",
}


class YamlParseError(ValueError):
    """Raised when YAML frontmatter or sidecar configuration has invalid syntax."""


def _yaml_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "mapping"
    return type(value).__name__


def _is_within(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _read_utf8_file(path: pathlib.Path, rel: pathlib.Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError as exc:
        return None, f"{rel}: Unable to decode file as UTF-8 ({exc})"
    except OSError as exc:
        return None, f"{rel}: Unable to read file ({exc})"


# ---------------------------------------------------------------------------
# Minimal YAML 1.2 subset parser (stdlib only)
# ---------------------------------------------------------------------------


def _line_indent(raw: str, line_no: int) -> int:
    indent = 0
    for ch in raw:
        if ch == " ":
            indent += 1
        elif ch == "\t":
            raise YamlParseError(f"Line {line_no}: Tab characters are not allowed for YAML indentation")
        else:
            break
    return indent


def _is_blank_or_comment(raw: str) -> bool:
    stripped = raw.strip(" \t")
    return not stripped or stripped.startswith("#")


def _next_meaningful_line(lines: list[str], start: int) -> int:
    idx = start
    while idx < len(lines) and _is_blank_or_comment(lines[idx]):
        idx += 1
    return idx


def _strip_plain_inline_comment(text: str) -> tuple[str, bool]:
    """Strip trailing '#' comment from an unquoted YAML plain scalar line.

    In YAML 1.2, quotes (' or \") inside a plain scalar are literal characters and
    never start a quoted string; any '#' at index 0 or preceded by space/tab begins a comment.
    """
    for i, ch in enumerate(text):
        if ch == "#" and (i == 0 or text[i - 1] in (" ", "\t")):
            return text[:i].rstrip(" \t"), True
    return text.rstrip(" \t"), False


def _has_unquoted_mapping_colon(text: str) -> bool:
    for i, ch in enumerate(text):
        if ch == ":" and (i + 1 == len(text) or text[i + 1] in (" ", "\t")):
            return True
    return False


def _decode_double_quoted_segment(segment: str, line_no: int) -> tuple[str, int | None, bool]:
    """Scan a double-quoted segment starting after opening '"' (or on continuation line).

    Returns (decoded_text, closing_index_in_segment_or_None, ended_with_line_escape).
    """
    out: list[str] = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if ch == '"':
            return "".join(out), i, False
        if ch == "\\":
            if i + 1 == n:
                return "".join(out), None, True
            esc = segment[i + 1]
            if esc in _DOUBLE_QUOTE_ESCAPES:
                out.append(_DOUBLE_QUOTE_ESCAPES[esc])
                i += 2
            elif esc in ("x", "u", "U"):
                hex_len = {"x": 2, "u": 4, "U": 8}[esc]
                hex_part = segment[i + 2 : i + 2 + hex_len]
                if len(hex_part) != hex_len or not re.fullmatch(r"[0-9a-fA-F]+", hex_part):
                    raise YamlParseError(
                        f"Line {line_no}: Invalid '\\{esc}' unicode escape in double-quoted string"
                    )
                code_point = int(hex_part, 16)
                if code_point > 0x10FFFF:
                    raise YamlParseError(f"Line {line_no}: Unicode escape out of range")
                out.append(chr(code_point))
                i += 2 + hex_len
            else:
                raise YamlParseError(
                    f"Line {line_no}: Unknown escape sequence '\\{esc}' in double-quoted string"
                )
        else:
            out.append(ch)
            i += 1
    return "".join(out), None, False


def _validate_trailing_after_token(rem: str, line_no: int, context: str) -> None:
    if not rem or rem.strip(" \t") == "":
        return
    if rem[0] in (" ", "\t") and rem.lstrip(" \t").startswith("#"):
        return
    raise YamlParseError(f"Line {line_no}: Unexpected trailing content after {context}: {rem.strip()!r}")


def _parse_double_quoted(
    lines: list[str],
    idx: int,
    start_text: str,
    key_indent: int,
    base_line_no: int,
) -> tuple[str, int]:
    start_line_no = base_line_no + idx
    decoded, close_idx, line_escaped = _decode_double_quoted_segment(start_text[1:], start_line_no)
    if close_idx is not None:
        _validate_trailing_after_token(start_text[1 + close_idx + 1 :], start_line_no, "double-quoted string")
        return decoded, idx + 1

    parts = [decoded]
    j = idx + 1
    while True:
        blank_count = 0
        while j < len(lines) and lines[j].strip(" \t") == "":
            blank_count += 1
            j += 1
        if j >= len(lines):
            raise YamlParseError(f"Line {start_line_no}: Unclosed double-quoted string")
        line_no = base_line_no + j
        cont_indent = _line_indent(lines[j], line_no)
        if cont_indent <= key_indent:
            raise YamlParseError(f"Line {start_line_no}: Unclosed double-quoted string")
        if line_escaped:
            if blank_count > 0:
                parts.append("\n" * blank_count)
        else:
            parts[-1] = parts[-1].rstrip(" \t")
            parts.append("\n" * blank_count if blank_count > 0 else " ")
        cont = lines[j][cont_indent:]
        seg, close_idx, line_escaped = _decode_double_quoted_segment(cont, line_no)
        parts.append(seg)
        if close_idx is not None:
            _validate_trailing_after_token(cont[close_idx + 1 :], line_no, "double-quoted string")
            return "".join(parts), j + 1
        j += 1


def _scan_single_quoted_segment(segment: str) -> tuple[str, int | None]:
    out: list[str] = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if ch == "'":
            if i + 1 < n and segment[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i
        out.append(ch)
        i += 1
    return "".join(out), None


def _parse_single_quoted(
    lines: list[str],
    idx: int,
    start_text: str,
    key_indent: int,
    base_line_no: int,
) -> tuple[str, int]:
    start_line_no = base_line_no + idx
    decoded, close_idx = _scan_single_quoted_segment(start_text[1:])
    if close_idx is not None:
        _validate_trailing_after_token(start_text[1 + close_idx + 1 :], start_line_no, "single-quoted string")
        return decoded, idx + 1

    parts = [decoded]
    j = idx + 1
    while True:
        blank_count = 0
        while j < len(lines) and lines[j].strip(" \t") == "":
            blank_count += 1
            j += 1
        if j >= len(lines):
            raise YamlParseError(f"Line {start_line_no}: Unclosed single-quoted string")
        line_no = base_line_no + j
        cont_indent = _line_indent(lines[j], line_no)
        if cont_indent <= key_indent:
            raise YamlParseError(f"Line {start_line_no}: Unclosed single-quoted string")
        parts[-1] = parts[-1].rstrip(" \t")
        parts.append("\n" * blank_count if blank_count > 0 else " ")
        cont = lines[j][cont_indent:]
        seg, close_idx = _scan_single_quoted_segment(cont)
        parts.append(seg)
        if close_idx is not None:
            _validate_trailing_after_token(cont[close_idx + 1 :], line_no, "single-quoted string")
            return "".join(parts), j + 1
        j += 1


def _fold_block_lines(core_lines: list[str]) -> str:
    out: list[str] = []
    n = len(core_lines)
    i = 0
    while i < n:
        line = core_lines[i]
        if line == "":
            j = i
            while j < n and core_lines[j] == "":
                j += 1
            empty_count = j - i
            prev_more = i > 0 and core_lines[i - 1].startswith((" ", "\t"))
            next_more = j < n and core_lines[j].startswith((" ", "\t"))
            if i == 0:
                out.append("\n" * empty_count)
            elif prev_more or next_more:
                out.append("\n" * (empty_count + 1))
            else:
                out.append("\n" * empty_count)
            i = j
        else:
            if i > 0 and core_lines[i - 1] != "":
                prev_more = core_lines[i - 1].startswith((" ", "\t"))
                curr_more = line.startswith((" ", "\t"))
                out.append("\n" if (prev_more or curr_more) else " ")
            out.append(line)
            i += 1
    return "".join(out)


def _parse_block_scalar(
    lines: list[str],
    idx: int,
    header_text: str,
    parent_indent: int,
    base_line_no: int,
) -> tuple[str, int]:
    line_no = base_line_no + idx
    match = BLOCK_SCALAR_HEADER_RE.match(header_text)
    if not match:
        raise YamlParseError(f"Line {line_no}: Invalid block scalar header {header_text!r}")

    style = match.group("style")
    mod = match.group("mod")
    chomp = ""
    explicit_indent: int | None = None
    for ch in mod:
        if ch in ("-", "+"):
            chomp = ch
        elif ch.isdigit():
            explicit_indent = int(ch)

    j = idx + 1
    content_indent: int | None = (
        parent_indent + explicit_indent if explicit_indent is not None else None
    )

    if content_indent is None:
        probe = j
        max_blank_spaces = 0
        while probe < len(lines) and lines[probe].strip(" ") == "":
            max_blank_spaces = max(max_blank_spaces, len(lines[probe]))
            probe += 1
        if probe < len(lines):
            probe_line = lines[probe]
            sp = len(probe_line) - len(probe_line.lstrip(" "))
            if sp < len(probe_line) and probe_line[sp] == "\t" and sp <= parent_indent:
                raise YamlParseError(
                    f"Line {base_line_no + probe}: Tab characters are not allowed for YAML indentation"
                )
            if sp > parent_indent:
                if max_blank_spaces > sp:
                    raise YamlParseError(
                        f"Line {base_line_no + probe}: Leading blank line exceeds block scalar indentation"
                    )
                content_indent = sp

    raw_block_lines: list[str] = []
    if content_indent is None:
        while j < len(lines) and lines[j].strip(" ") == "":
            raw_block_lines.append("")
            j += 1
    else:
        while j < len(lines):
            raw = lines[j]
            if raw.strip(" ") == "":
                raw_block_lines.append(raw[content_indent:] if len(raw) > content_indent else "")
                j += 1
                continue
            sp = len(raw) - len(raw.lstrip(" "))
            if sp < content_indent:
                break
            raw_block_lines.append(raw[content_indent:])
            j += 1

    trailing_empties = 0
    while raw_block_lines and raw_block_lines[-1] == "":
        raw_block_lines.pop()
        trailing_empties += 1

    if not raw_block_lines:
        return ("\n" * trailing_empties) if chomp == "+" else "", j

    body = "\n".join(raw_block_lines) if style == "|" else _fold_block_lines(raw_block_lines)
    if chomp == "-":
        return body, j
    if chomp == "+":
        return body + ("\n" * (1 + trailing_empties)), j
    return body + "\n", j


def _parse_scalar_token(raw: str) -> Any:
    if raw == "" or raw in YAML_NULL_TOKENS:
        return None
    if raw in YAML_TRUE_TOKENS:
        return True
    if raw in YAML_FALSE_TOKENS:
        return False
    return raw


def _split_flow_items(inner: str, line_no: int) -> list[str]:
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    in_single = False
    in_double = False
    at_token_start = True
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if in_double:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(inner[i + 1])
                i += 2
                continue
            if ch == '"':
                in_double = False
        elif in_single:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and inner[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_single = False
        else:
            if ch == "#" and (i == 0 or inner[i - 1] in (" ", "\t", "\n")):
                while i < n and inner[i] != "\n":
                    i += 1
                continue
            if ch == "\n":
                if buf and buf[-1] not in (" ", "\t"):
                    buf.append(" ")
                i += 1
                while i < n and inner[i] in (" ", "\t"):
                    i += 1
                continue
            if ch == "&" and at_token_start:
                buf.append(ch)
                i += 1
                while i < n and inner[i] not in (" ", "\t", "\r", "\n", ",", "[", "]", "{", "}"):
                    buf.append(inner[i])
                    i += 1
                continue
            if ch == '"' and at_token_start:
                in_double = True
                at_token_start = False
                buf.append(ch)
            elif ch == "'" and at_token_start:
                in_single = True
                at_token_start = False
                buf.append(ch)
            elif ch in ("[", "{"):
                depth += 1
                at_token_start = True
                buf.append(ch)
            elif ch in ("]", "}"):
                depth -= 1
                if depth < 0:
                    raise YamlParseError(f"Line {line_no}: Unmatched closing '{ch}' in flow collection")
                at_token_start = False
                buf.append(ch)
            elif ch == "," and depth == 0:
                items.append("".join(buf).strip())
                buf = []
                at_token_start = True
            elif ch == ",":
                at_token_start = True
                buf.append(ch)
            elif ch == ":" and (i + 1 == n or inner[i + 1] in (" ", "\t", "\n")):
                at_token_start = True
                buf.append(ch)
            elif ch in (" ", "\t", "\r"):
                buf.append(ch)
            else:
                at_token_start = False
                buf.append(ch)
        i += 1
    if in_single or in_double or depth != 0:
        raise YamlParseError(f"Line {line_no}: Malformed flow collection")
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _parse_flow_value(token: str, line_no: int, anchors: dict[str, Any]) -> Any:
    token = token.strip()
    if not token:
        return None
    if token.startswith("*"):
        alias_name = token[1:]
        if not alias_name or not YAML_ANCHOR_NAME_RE.match(alias_name):
            raise YamlParseError(f"Line {line_no}: Invalid YAML alias {token!r} in flow collection")
        if alias_name not in anchors:
            raise YamlParseError(f"Line {line_no}: Undefined YAML alias '*{alias_name}'")
        return copy.deepcopy(anchors[alias_name])
    if token.startswith("&"):
        anchor_match = YAML_ANCHOR_PREFIX_RE.match(token)
        if not anchor_match:
            raise YamlParseError(f"Line {line_no}: Invalid YAML anchor syntax {token!r} in flow collection")
        anchor_name = anchor_match.group(1)
        after_anchor = (anchor_match.group(2) or "").strip()
        if after_anchor.startswith(("*", "&")):
            raise YamlParseError(
                f"Line {line_no}: Anchor '&{anchor_name}' cannot directly precede another anchor or alias"
            )
        val = _parse_flow_value(after_anchor, line_no, anchors) if after_anchor else None
        anchors[anchor_name] = copy.deepcopy(val)
        return val
    if token.startswith('"'):
        decoded, close_idx, _ = _decode_double_quoted_segment(token[1:], line_no)
        if close_idx is None:
            raise YamlParseError(f"Line {line_no}: Unclosed double-quoted string in flow collection")
        if token[1 + close_idx + 1 :].strip():
            raise YamlParseError(f"Line {line_no}: Trailing content after double-quoted string in flow collection")
        return decoded
    if token.startswith("'"):
        decoded, close_idx = _scan_single_quoted_segment(token[1:])
        if close_idx is None:
            raise YamlParseError(f"Line {line_no}: Unclosed single-quoted string in flow collection")
        if token[1 + close_idx + 1 :].strip():
            raise YamlParseError(f"Line {line_no}: Trailing content after single-quoted string in flow collection")
        return decoded
    if token.startswith("["):
        if not token.endswith("]"):
            raise YamlParseError(f"Line {line_no}: Unclosed flow sequence '['")
        inner = token[1:-1].strip()
        if not inner:
            return []
        return [_parse_flow_value(item, line_no, anchors) for item in _split_flow_items(inner, line_no)]
    if token.startswith("{"):
        if not token.endswith("}"):
            raise YamlParseError(f"Line {line_no}: Unclosed flow mapping '{{'")
        inner = token[1:-1].strip()
        if not inner:
            return {}
        result: dict[str, Any] = {}
        for entry in _split_flow_items(inner, line_no):
            pair = _split_mapping_line(entry, line_no)
            if pair is None:
                raise YamlParseError(f"Line {line_no}: Invalid entry {entry!r} in flow mapping")
            k, v = pair
            if k in result:
                raise YamlParseError(f"Line {line_no}: Duplicate mapping key {k!r} in flow mapping")
            result[k] = _parse_flow_value(v, line_no, anchors)
        return result
    if _has_unquoted_mapping_colon(token):
        raise YamlParseError(f"Line {line_no}: Unexpected ':' in flow scalar {token!r}")
    return _parse_scalar_token(token)


def _find_flow_close(text: str, open_ch: str, close_ch: str) -> int | None:
    depth = 0
    in_single = False
    in_double = False
    at_token_start = True
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_double:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_double = False
        elif in_single:
            if ch == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
        else:
            if ch == "#" and (i == 0 or text[i - 1] in (" ", "\t", "\n")):
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if ch == "&" and at_token_start:
                i += 1
                while i < n and text[i] not in (" ", "\t", "\r", "\n", ",", "[", "]", "{", "}"):
                    i += 1
                continue
            if ch == '"' and at_token_start:
                in_double = True
                at_token_start = False
            elif ch == "'" and at_token_start:
                in_single = True
                at_token_start = False
            elif ch in ("[", "{"):
                if ch == open_ch:
                    depth += 1
                at_token_start = True
            elif ch in ("]", "}"):
                if ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        return i
                at_token_start = False
            elif ch == ",":
                at_token_start = True
            elif ch == ":" and (i + 1 == n or text[i + 1] in (" ", "\t", "\n")):
                at_token_start = True
            elif ch in (" ", "\t", "\r", "\n"):
                pass
            else:
                at_token_start = False
        i += 1
    return None


def _parse_flow_collection(
    lines: list[str],
    idx: int,
    start_text: str,
    parent_indent: int,
    base_line_no: int,
    anchors: dict[str, Any],
) -> tuple[Any, int]:
    start_line_no = base_line_no + idx
    open_ch = start_text[0]
    close_ch = "]" if open_ch == "[" else "}"
    buf = start_text
    j = idx
    while True:
        close_idx = _find_flow_close(buf, open_ch, close_ch)
        if close_idx is not None:
            _validate_trailing_after_token(
                buf[close_idx + 1 :], base_line_no + j, f"flow collection '{open_ch}...{close_ch}'"
            )
            return _parse_flow_value(buf[: close_idx + 1], start_line_no, anchors), j + 1
        j += 1
        while j < len(lines) and _is_blank_or_comment(lines[j]):
            j += 1
        if j >= len(lines):
            raise YamlParseError(f"Line {start_line_no}: Unclosed flow collection '{open_ch}'")
        cont_indent = _line_indent(lines[j], base_line_no + j)
        cont_text = lines[j][cont_indent:]
        if cont_indent < parent_indent or (
            cont_indent == parent_indent and not cont_text.startswith(close_ch)
        ):
            raise YamlParseError(f"Line {start_line_no}: Unclosed flow collection '{open_ch}'")
        buf = f"{buf}\n{cont_text}"


def _split_mapping_line(content: str, line_no: int) -> tuple[str, str] | None:
    if not content:
        return None
    if content.startswith(("#", "|", ">", "[", "{")) or content == "-" or content.startswith(("- ", "-\t")):
        return None
    if content.startswith('"'):
        key, close_idx, _ = _decode_double_quoted_segment(content[1:], line_no)
        if close_idx is None:
            raise YamlParseError(f"Line {line_no}: Unclosed double-quoted mapping key")
        after = content[1 + close_idx + 1 :].lstrip(" \t")
        if after.startswith(":") and (len(after) == 1 or after[1] in (" ", "\t")):
            return key, after[1:].lstrip(" \t")
        return None
    if content.startswith("'"):
        key, close_idx = _scan_single_quoted_segment(content[1:])
        if close_idx is None:
            raise YamlParseError(f"Line {line_no}: Unclosed single-quoted mapping key")
        after = content[1 + close_idx + 1 :].lstrip(" \t")
        if after.startswith(":") and (len(after) == 1 or after[1] in (" ", "\t")):
            return key, after[1:].lstrip(" \t")
        return None

    for i, ch in enumerate(content):
        if ch == "#" and (i == 0 or content[i - 1] in (" ", "\t")):
            return None
        if ch == ":" and (i + 1 == len(content) or content[i + 1] in (" ", "\t")):
            raw_key = content[:i].strip(" \t")
            if not raw_key:
                raise YamlParseError(f"Line {line_no}: Empty mapping key before ':'")
            if raw_key.startswith("*"):
                raise YamlParseError(f"Line {line_no}: Undefined or unsupported YAML alias key {raw_key!r}")
            return raw_key, content[i + 1 :].lstrip(" \t")
    return None


def _parse_value(
    lines: list[str],
    idx: int,
    rest: str,
    parent_indent: int,
    base_line_no: int,
    anchors: dict[str, Any],
) -> tuple[Any, int]:
    rest = rest.lstrip(" \t")
    if not rest or rest.startswith("#"):
        j = _next_meaningful_line(lines, idx + 1)
        if j >= len(lines):
            return None, j
        next_line_no = base_line_no + j
        next_indent = _line_indent(lines[j], next_line_no)
        next_content = lines[j][next_indent:]
        is_seq = next_content == "-" or next_content.startswith(("- ", "-\t"))
        if next_indent == parent_indent and is_seq:
            return _parse_block_sequence(lines, j, next_indent, base_line_no, anchors)
        if next_indent > parent_indent:
            if is_seq:
                return _parse_block_sequence(lines, j, next_indent, base_line_no, anchors)
            if _split_mapping_line(next_content, next_line_no) is not None:
                return _parse_block_mapping(lines, j, next_indent, base_line_no, anchors)
            return _parse_value(lines, j, next_content, parent_indent, base_line_no, anchors)
        return None, idx + 1

    if rest.startswith('"'):
        return _parse_double_quoted(lines, idx, rest, parent_indent, base_line_no)
    if rest.startswith("'"):
        return _parse_single_quoted(lines, idx, rest, parent_indent, base_line_no)
    if rest[0] in ("|", ">"):
        return _parse_block_scalar(lines, idx, rest, parent_indent, base_line_no)
    if rest[0] in ("[", "{"):
        return _parse_flow_collection(lines, idx, rest, parent_indent, base_line_no, anchors)
    if rest.startswith("*"):
        alias_part, _ = _strip_plain_inline_comment(rest)
        alias_name = alias_part[1:]
        if not alias_name or not YAML_ANCHOR_NAME_RE.match(alias_name):
            raise YamlParseError(f"Line {base_line_no + idx}: Invalid YAML alias {alias_part!r}")
        if alias_name not in anchors:
            raise YamlParseError(f"Line {base_line_no + idx}: Undefined YAML alias '*{alias_name}'")
        return copy.deepcopy(anchors[alias_name]), idx + 1
    if rest.startswith("&"):
        anchor_match = YAML_ANCHOR_PREFIX_RE.match(rest)
        if not anchor_match:
            raise YamlParseError(f"Line {base_line_no + idx}: Invalid YAML anchor syntax {rest!r}")
        anchor_name = anchor_match.group(1)
        after_anchor = (anchor_match.group(2) or "").lstrip(" \t")
        if after_anchor.startswith(("*", "&")):
            raise YamlParseError(
                f"Line {base_line_no + idx}: Anchor '&{anchor_name}' cannot directly precede another anchor or alias"
            )
        val, next_idx = _parse_value(lines, idx, after_anchor, parent_indent, base_line_no, anchors)
        anchors[anchor_name] = copy.deepcopy(val)
        return val, next_idx

    first_part, had_comment = _strip_plain_inline_comment(rest)
    if _has_unquoted_mapping_colon(first_part):
        raise YamlParseError(
            f"Line {base_line_no + idx}: Unexpected ':' in unquoted plain scalar {first_part!r}"
        )

    if had_comment:
        return _parse_scalar_token(first_part), idx + 1

    parts = [first_part]
    j = idx + 1
    last_consumed = idx + 1
    while j < len(lines):
        blank_count = 0
        probe = j
        while probe < len(lines) and lines[probe].strip(" \t") == "":
            blank_count += 1
            probe += 1
        if probe >= len(lines):
            break
        if lines[probe].strip(" \t").startswith("#"):
            break
        probe_line_no = base_line_no + probe
        cont_indent = _line_indent(lines[probe], probe_line_no)
        if cont_indent <= parent_indent:
            break
        cont_text = lines[probe][cont_indent:]
        cont_part, cont_had_comment = _strip_plain_inline_comment(cont_text)
        if _has_unquoted_mapping_colon(cont_part):
            raise YamlParseError(
                f"Line {probe_line_no}: Unexpected mapping syntax or ':' in multiline plain scalar"
            )
        parts.append("\n" * blank_count if blank_count > 0 else " ")
        parts.append(cont_part)
        last_consumed = probe + 1
        j = probe + 1
        if cont_had_comment:
            break

    if len(parts) == 1:
        return _parse_scalar_token(first_part), last_consumed
    return "".join(parts), last_consumed


def _parse_block_sequence(
    lines: list[str],
    start: int,
    expected_indent: int,
    base_line_no: int,
    anchors: dict[str, Any],
) -> tuple[list[Any], int]:
    items: list[Any] = []
    idx = start
    while idx < len(lines):
        idx = _next_meaningful_line(lines, idx)
        if idx >= len(lines):
            break
        line_no = base_line_no + idx
        indent = _line_indent(lines[idx], line_no)
        if indent < expected_indent:
            break
        if indent > expected_indent:
            raise YamlParseError(
                f"Line {line_no}: Unexpected indentation in sequence (got {indent}, expected {expected_indent})"
            )
        content = lines[idx][indent:]
        if not (content == "-" or content.startswith(("- ", "-\t"))):
            break

        after_dash = content[1:]
        if not after_dash or after_dash.strip(" \t") == "" or after_dash.lstrip(" \t").startswith("#"):
            val, idx = _parse_value(lines, idx, "", expected_indent, base_line_no, anchors)
            items.append(val)
            continue

        leading_after = len(after_dash) - len(after_dash.lstrip(" "))
        if leading_after < len(after_dash) and after_dash[leading_after] == "\t":
            raise YamlParseError(f"Line {line_no}: Tab characters are not allowed after '-' sequence indicator")
        rest = after_dash[leading_after:]
        rest_indent = expected_indent + 1 + leading_after

        if rest == "-" or rest.startswith(("- ", "-\t")):
            synthetic_lines = list(lines)
            synthetic_lines[idx] = (" " * rest_indent) + rest
            val, idx = _parse_block_sequence(synthetic_lines, idx, rest_indent, base_line_no, anchors)
            items.append(val)
            continue

        pair = _split_mapping_line(rest, line_no)
        if pair is not None:
            k, v_rest = pair
            item_map: dict[str, Any] = {}
            val, idx = _parse_value(lines, idx, v_rest, rest_indent, base_line_no, anchors)
            item_map[k] = val
            next_idx = _next_meaningful_line(lines, idx)
            if next_idx < len(lines):
                next_indent = _line_indent(lines[next_idx], base_line_no + next_idx)
                if next_indent == rest_indent:
                    more_map, idx = _parse_block_mapping(
                        lines, next_idx, rest_indent, base_line_no, anchors, existing_keys={k}
                    )
                    item_map.update(more_map)
            items.append(item_map)
            continue

        val, idx = _parse_value(lines, idx, rest, expected_indent, base_line_no, anchors)
        items.append(val)
    return items, idx


def _parse_block_mapping(
    lines: list[str],
    start: int,
    expected_indent: int,
    base_line_no: int,
    anchors: dict[str, Any],
    existing_keys: set[str] | None = None,
) -> tuple[dict[str, Any], int]:
    data: dict[str, Any] = {}
    seen = set(existing_keys) if existing_keys else set()
    idx = start
    while idx < len(lines):
        idx = _next_meaningful_line(lines, idx)
        if idx >= len(lines):
            break
        line_no = base_line_no + idx
        indent = _line_indent(lines[idx], line_no)
        if indent < expected_indent:
            break
        if indent > expected_indent:
            raise YamlParseError(
                f"Line {line_no}: Unexpected indentation (got {indent}, expected {expected_indent})"
            )
        content = lines[idx][indent:]
        pair = _split_mapping_line(content, line_no)
        if pair is None:
            raise YamlParseError(f"Line {line_no}: Expected 'key: value' mapping entry, got {content!r}")
        key, rest = pair
        if key in seen:
            raise YamlParseError(f"Line {line_no}: Duplicate mapping key {key!r}")
        seen.add(key)
        val, idx = _parse_value(lines, idx, rest, expected_indent, base_line_no, anchors)
        data[key] = val
    return data, idx


def parse_yaml_mapping(
    lines: list[str],
    base_line_no: int = 1,
    allow_doc_markers: bool = False,
) -> dict[str, Any]:
    """Parse a YAML mapping using the documented pure-stdlib subset."""
    start = _next_meaningful_line(lines, 0)
    end = len(lines)
    if allow_doc_markers and start < end and lines[start].rstrip(" \t") == "---":
        start = _next_meaningful_line(lines, start + 1)
    if allow_doc_markers:
        for i in range(start, end):
            if lines[i].rstrip(" \t") == "...":
                end = i
                break

    sliced = lines[:end]
    start = _next_meaningful_line(sliced, start)
    if start >= len(sliced):
        return {}

    root_indent = _line_indent(sliced[start], base_line_no + start)
    if root_indent != 0:
        raise YamlParseError(
            f"Line {base_line_no + start}: Root YAML mapping must start at column 0 (got indent {root_indent})"
        )
    anchors: dict[str, Any] = {}
    data, next_idx = _parse_block_mapping(sliced, start, 0, base_line_no, anchors)
    next_idx = _next_meaningful_line(sliced, next_idx)
    if next_idx < len(sliced):
        raise YamlParseError(
            f"Line {base_line_no + next_idx}: Unexpected trailing YAML content {sliced[next_idx]!r}"
        )
    return data


def parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, int, str | None]:
    """Parse YAML frontmatter delimited by '---' lines."""
    lines = text.splitlines()
    if not lines or lines[0].rstrip(" \t") != "---":
        return None, 0, "Missing opening '---' YAML frontmatter delimiter on line 1"

    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip(" \t") == "---":
            end_idx = idx
            break
    if end_idx is None:
        return None, 0, "Missing closing '---' YAML frontmatter delimiter"

    try:
        data = parse_yaml_mapping(lines[1:end_idx], base_line_no=2, allow_doc_markers=False)
    except YamlParseError as exc:
        return None, 0, f"Malformed YAML frontmatter ({exc})"

    body_line_count = len(lines) - (end_idx + 1)
    return data, body_line_count, None


# ---------------------------------------------------------------------------
# SKILL.md and Codex agents/openai.yaml validators
# ---------------------------------------------------------------------------


def check_openai_sidecar(
    sidecar: pathlib.Path,
    root: pathlib.Path,
    skill_name: str,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    sc_rel = sidecar.relative_to(root)

    sc_text, read_err = _read_utf8_file(sidecar, sc_rel)
    if read_err or sc_text is None:
        errors.append(read_err or f"{sc_rel}: Unable to read file")
        return errors, warnings

    if sc_text.startswith("\ufeff"):
        warnings.append(
            f"{sc_rel}: UTF-8 BOM detected at start of file; prefer UTF-8 without BOM for strict YAML parsers"
        )
        sc_text = sc_text[1:]

    try:
        data = parse_yaml_mapping(sc_text.splitlines(), base_line_no=1, allow_doc_markers=True)
    except YamlParseError as exc:
        errors.append(f"{sc_rel}: Malformed YAML syntax ({exc})")
        return errors, warnings

    if "interface" in data and data["interface"] is not None:
        interface = data["interface"]
        if not isinstance(interface, dict):
            errors.append(
                f"{sc_rel}: 'interface' section must be a YAML mapping, got {_yaml_type_name(interface)}"
            )
        else:
            if "short_description" in interface:
                raw_sdesc = interface["short_description"]
                if raw_sdesc is None:
                    warnings.append(
                        f"{sc_rel}: interface.short_description is empty (recommended 25-64 chars)"
                    )
                elif not isinstance(raw_sdesc, str):
                    errors.append(
                        f"{sc_rel}: interface.short_description must be a string, "
                        f"got {_yaml_type_name(raw_sdesc)}"
                    )
                else:
                    sdesc = raw_sdesc.strip()
                    if not (25 <= len(sdesc) <= 64):
                        warnings.append(
                            f"{sc_rel}: interface.short_description length is {len(sdesc)} chars "
                            "(recommended 25-64)"
                        )

            if "default_prompt" in interface:
                raw_prompt = interface["default_prompt"]
                if raw_prompt is None:
                    if skill_name:
                        warnings.append(
                            f"{sc_rel}: interface.default_prompt is empty and omits '${skill_name}'"
                        )
                elif not isinstance(raw_prompt, str):
                    errors.append(
                        f"{sc_rel}: interface.default_prompt must be a string, "
                        f"got {_yaml_type_name(raw_prompt)}"
                    )
                elif skill_name:
                    token_re = re.compile(rf"\${re.escape(skill_name)}(?![A-Za-z0-9_-])")
                    if not token_re.search(raw_prompt):
                        warnings.append(
                            f"{sc_rel}: interface.default_prompt omits explicit '${skill_name}' invocation token"
                        )

    if "policy" in data and data["policy"] is not None:
        policy = data["policy"]
        if not isinstance(policy, dict):
            errors.append(
                f"{sc_rel}: 'policy' section must be a YAML mapping, got {_yaml_type_name(policy)}"
            )
        elif "allow_implicit_invocation" in policy:
            allow_implicit = policy["allow_implicit_invocation"]
            if not isinstance(allow_implicit, bool):
                errors.append(
                    f"{sc_rel}: policy.allow_implicit_invocation must be an unquoted YAML boolean "
                    f"(true/false), got {_yaml_type_name(allow_implicit)} ({allow_implicit!r})"
                )

    return errors, warnings


def check_skill_file(
    skill_md: pathlib.Path,
    root: pathlib.Path,
) -> tuple[list[str], list[str], bool]:
    """Validate SKILL.md and its optional agents/openai.yaml sidecar.

    Returns (errors, warnings, was_readable).
    """
    errors: list[str] = []
    warnings: list[str] = []
    rel = skill_md.relative_to(root)
    parent_name = skill_md.parent.name

    text, read_err = _read_utf8_file(skill_md, rel)
    if read_err or text is None:
        errors.append(read_err or f"{rel}: Unable to read file")
        sidecar = skill_md.parent / "agents" / "openai.yaml"
        if sidecar.is_file():
            sc_e, sc_w = check_openai_sidecar(sidecar, root, parent_name)
            errors.extend(sc_e)
            warnings.extend(sc_w)
        return errors, warnings, False

    if text.startswith("\ufeff"):
        warnings.append(
            f"{rel}: UTF-8 BOM detected at start of file; although YAML permits BOM, "
            "strict frontmatter extractors (e.g. Codex) require '---' on the first line without BOM"
        )
        text = text[1:]

    fm, body_lines, err = parse_frontmatter(text)
    effective_name = parent_name
    if err or fm is None:
        errors.append(f"{rel}: {err}")
    else:
        raw_name = fm.get("name")
        if raw_name is None or (isinstance(raw_name, str) and not raw_name.strip()):
            errors.append(f"{rel}: Missing required frontmatter field 'name'")
        elif not isinstance(raw_name, str):
            errors.append(
                f"{rel}: Frontmatter field 'name' must be a string, got {_yaml_type_name(raw_name)}"
            )
        else:
            effective_name = raw_name
            if len(raw_name) > 64 or not NAME_PATTERN.match(raw_name):
                errors.append(
                    f"{rel}: Invalid 'name' ({raw_name!r}); must be <=64 chars and match ^[a-z0-9]+(-[a-z0-9]+)*$"
                )
            if raw_name != parent_name:
                errors.append(
                    f"{rel}: Frontmatter name '{raw_name}' does not match parent directory '{parent_name}'"
                )

        raw_desc = fm.get("description")
        if raw_desc is None:
            errors.append(f"{rel}: Missing or empty required frontmatter field 'description'")
        elif not isinstance(raw_desc, str):
            errors.append(
                f"{rel}: Frontmatter field 'description' must be a string, got {_yaml_type_name(raw_desc)}"
            )
        else:
            desc = raw_desc.strip()
            if not desc:
                errors.append(f"{rel}: Missing or empty required frontmatter field 'description'")
            elif len(desc) > 1024:
                errors.append(f"{rel}: 'description' length is {len(desc)} chars (exceeds 1024 limit)")

        if "compatibility" in fm:
            raw_compat = fm["compatibility"]
            if raw_compat is None:
                errors.append(
                    f"{rel}: Frontmatter field 'compatibility' is empty "
                    "(if provided, must be 1-500 non-empty chars)"
                )
            elif not isinstance(raw_compat, str):
                errors.append(
                    f"{rel}: Frontmatter field 'compatibility' must be a string, "
                    f"got {_yaml_type_name(raw_compat)}"
                )
            else:
                compat = raw_compat.strip()
                if not compat:
                    errors.append(
                        f"{rel}: Frontmatter field 'compatibility' is empty "
                        "(if provided, must be 1-500 non-empty chars)"
                    )
                elif len(compat) > 500:
                    errors.append(
                        f"{rel}: 'compatibility' length is {len(compat)} chars (exceeds 500 limit)"
                    )

        if "metadata" in fm:
            raw_meta = fm["metadata"]
            if not isinstance(raw_meta, dict):
                errors.append(
                    f"{rel}: Frontmatter field 'metadata' must be a key-value mapping, "
                    f"got {_yaml_type_name(raw_meta)}"
                )

        unknown_keys = sorted(set(fm.keys()) - STANDARD_SKILL_KEYS)
        if unknown_keys:
            warnings.append(
                f"{rel}: Non-standard frontmatter keys {unknown_keys} are outside the agentskills.io "
                "open specification and may reduce cross-client portability or fail strict validators; "
                "prefer standard keys (and agents/openai.yaml for Codex metadata)"
            )

        total_lines = len(text.splitlines())
        if total_lines >= 500:
            warnings.append(
                f"{rel}: Total length is {total_lines} lines (body {body_lines}); "
                "recommended < 500 lines with details moved to references/"
            )

    sidecar = skill_md.parent / "agents" / "openai.yaml"
    if sidecar.is_file():
        sc_e, sc_w = check_openai_sidecar(sidecar, root, effective_name)
        errors.extend(sc_e)
        warnings.extend(sc_w)

    return errors, warnings, True


# ---------------------------------------------------------------------------
# Markdown link extractor & validator
# ---------------------------------------------------------------------------


def _strip_fenced_code_blocks(text: str) -> str:
    out_lines: list[str] = []
    open_char: str | None = None
    open_len = 0

    for line in text.splitlines(keepends=True):
        raw_no_nl = line.rstrip("\r\n")
        nl = line[len(raw_no_nl) :]
        if open_char is None:
            m = FENCE_OPEN_RE.match(raw_no_nl)
            if m:
                fence = m.group("fence")
                info = m.group("info")
                if fence[0] == "`" and "`" in info:
                    out_lines.append(line)
                else:
                    open_char = fence[0]
                    open_len = len(fence)
                    out_lines.append(nl)
            else:
                out_lines.append(line)
        else:
            cm = FENCE_CLOSE_RE.match(raw_no_nl)
            if cm:
                fence = cm.group("fence")
                if fence[0] == open_char and len(fence) >= open_len:
                    open_char = None
                    open_len = 0
            out_lines.append(nl)
    return "".join(out_lines)


def _strip_inline_code_spans(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in ("\\", "`"):
            out.append(text[i : i + 2])
            i += 2
            continue
        if ch == "`":
            run_end = i
            while run_end < n and text[run_end] == "`":
                run_end += 1
            tick_len = run_end - i

            # Find matching run of exactly tick_len backticks before a paragraph break.
            search = run_end
            close_start: int | None = None
            while search < n:
                if text[search] == "\n" and search + 1 < n and text[search + 1] == "\n":
                    break
                if text[search] == "\r" and text[search : search + 4] == "\r\n\r\n":
                    break
                if text[search] == "`":
                    c_end = search
                    while c_end < n and text[c_end] == "`":
                        c_end += 1
                    if c_end - search == tick_len:
                        close_start = search
                        break
                    search = c_end
                else:
                    search += 1

            if close_start is not None:
                span = text[i : close_start + tick_len]
                nl_count = span.count("\n")
                out.append(" " + ("\n" * nl_count) + " " if nl_count else " ")
                i = close_start + tick_len
            else:
                out.append(text[i:run_end])
                i = run_end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _is_escaped(text: str, pos: int) -> bool:
    backslashes = 0
    i = pos - 1
    while i >= 0 and text[i] == "\\":
        backslashes += 1
        i -= 1
    return (backslashes % 2) == 1


def _has_matching_open_bracket(text: str, close_bracket_pos: int) -> bool:
    depth = 1
    i = close_bracket_pos - 1
    while i >= 0:
        if text[i] == "\n" and i > 0 and text[i - 1] == "\n":
            return False
        if text[i] == "]" and not _is_escaped(text, i):
            depth += 1
        elif text[i] == "[" and not _is_escaped(text, i):
            depth -= 1
            if depth == 0:
                return True
        i -= 1
    return False


def _consume_optional_title(text: str, pos: int, stop_at_eol: bool) -> int | None:
    """Consume an optional Markdown link title starting at pos (after whitespace).

    Returns index immediately after title, or None if invalid.
    """
    n = len(text)
    if pos >= n:
        return pos
    quote = text[pos]
    if quote not in ('"', "'", "("):
        return None
    end_ch = ")" if quote == "(" else quote
    i = pos + 1
    while i < n:
        ch = text[i]
        if stop_at_eol and ch in ("\r", "\n"):
            return None
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == end_ch:
            return i + 1
        if quote == "(" and ch == "(":
            return None
        i += 1
    return None


def _parse_inline_link_dest(text: str, open_paren_pos: int) -> tuple[str, int] | None:
    """Parse '(dest "optional title")' starting at open_paren_pos.

    Returns (destination, end_pos_after_close_paren) or None.
    """
    n = len(text)
    i = open_paren_pos + 1
    while i < n and text[i] in (" ", "\t", "\r", "\n"):
        i += 1
    if i >= n:
        return None
    if text[i] == ")":
        return "", i + 1

    # Attempt CommonMark angle-bracket destination <...>
    if text[i] == "<":
        j = i + 1
        buf: list[str] = []
        angle_ok = False
        while j < n:
            ch = text[j]
            if ch in ("\r", "\n", "<"):
                break
            if ch == "\\" and j + 1 < n and text[j + 1] in ("<", ">", "\\"):
                buf.append(text[j + 1])
                j += 2
                continue
            if ch == ">":
                angle_ok = True
                j += 1
                break
            buf.append(ch)
            j += 1

        if angle_ok:
            k = j
            while k < n and text[k] in (" ", "\t", "\r", "\n"):
                k += 1
            if k < n and text[k] == ")":
                return "".join(buf), k + 1
            if k > j and k < n and text[k] in ('"', "'", "("):
                after_title = _consume_optional_title(text, k, stop_at_eol=False)
                if after_title is not None:
                    while after_title < n and text[after_title] in (" ", "\t", "\r", "\n"):
                        after_title += 1
                    if after_title < n and text[after_title] == ")":
                        return "".join(buf), after_title + 1

    # Normal destination with balanced parentheses and optional title
    dest_buf: list[str] = []
    paren_depth = 0
    j = i
    while j < n:
        ch = text[j]
        if ch in (" ", "\t", "\r", "\n"):
            break
        if ch == "\\" and j + 1 < n and text[j + 1] in ASCII_PUNCTUATION:
            dest_buf.append(text[j + 1])
            j += 2
            continue
        if ch == "(":
            paren_depth += 1
            dest_buf.append(ch)
            j += 1
            continue
        if ch == ")":
            if paren_depth == 0:
                return "".join(dest_buf), j + 1
            paren_depth -= 1
            dest_buf.append(ch)
            j += 1
            continue
        dest_buf.append(ch)
        j += 1

    if paren_depth != 0 or not dest_buf:
        return None

    k = j
    while k < n and text[k] in (" ", "\t", "\r", "\n"):
        k += 1
    if k < n and text[k] == ")":
        return "".join(dest_buf), k + 1
    if k > j and k < n and text[k] in ('"', "'", "("):
        after_title = _consume_optional_title(text, k, stop_at_eol=False)
        if after_title is not None:
            while after_title < n and text[after_title] in (" ", "\t", "\r", "\n"):
                after_title += 1
            if after_title < n and text[after_title] == ")":
                return "".join(dest_buf), after_title + 1
    return None


def _parse_ref_def_dest(rest: str) -> str | None:
    """Parse 'dest ["optional title"]' from a reference-style link definition line."""
    s = rest.strip(" \t")
    if not s:
        return None
    n = len(s)

    if s[0] == "<":
        j = 1
        buf: list[str] = []
        angle_ok = False
        while j < n:
            ch = s[j]
            if ch == "<":
                break
            if ch == "\\" and j + 1 < n and s[j + 1] in ("<", ">", "\\"):
                buf.append(s[j + 1])
                j += 2
                continue
            if ch == ">":
                angle_ok = True
                j += 1
                break
            buf.append(ch)
            j += 1
        if angle_ok:
            rem = s[j:].strip(" \t")
            if not rem:
                return "".join(buf)
            if j < n and s[j] in (" ", "\t"):
                after_title = _consume_optional_title(rem, 0, stop_at_eol=True)
                if after_title is not None and rem[after_title:].strip(" \t") == "":
                    return "".join(buf)

    # Normal destination (whitespace-delimited) + optional title to EOL
    dest_buf: list[str] = []
    j = 0
    while j < n and s[j] not in (" ", "\t"):
        if s[j] == "\\" and j + 1 < n and s[j + 1] in ASCII_PUNCTUATION:
            dest_buf.append(s[j + 1])
            j += 2
            continue
        dest_buf.append(s[j])
        j += 1

    if not dest_buf:
        return None
    rem = s[j:].strip(" \t")
    if not rem:
        return "".join(dest_buf)
    after_title = _consume_optional_title(rem, 0, stop_at_eol=True)
    if after_title is not None and rem[after_title:].strip(" \t") == "":
        return "".join(dest_buf)
    return None


def _extract_markdown_links(text: str) -> list[str]:
    """Extract raw link destinations from Markdown text after stripping code."""
    cleaned = _strip_inline_code_spans(_strip_fenced_code_blocks(text))
    targets: list[str] = []

    # 1. Inline links: [label](dest "optional title")
    i = 0
    n = len(cleaned)
    while i < n - 1:
        if cleaned[i] == "]" and cleaned[i + 1] == "(" and not _is_escaped(cleaned, i):
            if _has_matching_open_bracket(cleaned, i):
                parsed = _parse_inline_link_dest(cleaned, i + 1)
                if parsed is not None:
                    dest, end_pos = parsed
                    targets.append(dest)
                    i = end_pos
                    continue
        i += 1

    # 2. Reference-style link definitions: [id]: dest ["optional title"]
    lines = cleaned.splitlines()
    idx = 0
    while idx < len(lines):
        m = REF_DEF_LINE_RE.match(lines[idx])
        if m:
            label = m.group("label").strip()
            if label and not label.startswith("^"):
                rest = m.group("rest").strip(" \t")
                if not rest and idx + 1 < len(lines):
                    next_line = lines[idx + 1].strip(" \t")
                    if next_line and not REF_DEF_LINE_RE.match(lines[idx + 1]):
                        parsed_ref = _parse_ref_def_dest(next_line)
                        if parsed_ref is not None:
                            targets.append(parsed_ref)
                            idx += 2
                            continue
                else:
                    parsed_ref = _parse_ref_def_dest(rest)
                    if parsed_ref is not None:
                        targets.append(parsed_ref)
        idx += 1

    return targets


def _is_root_or_drive_path(target: str) -> bool:
    """Return True for POSIX root ('/'), UNC/Windows root ('\\'), or Windows drive ('C:') paths."""
    return target.startswith(("/", "\\")) or bool(WINDOWS_DRIVE_RE.match(target))


def _strip_leading_frontmatter(text: str, is_skill_md: bool) -> str:
    """Strip YAML frontmatter at top of Markdown files so metadata isn't scanned as body links."""
    body = text[1:] if text.startswith("\ufeff") else text
    lines = body.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n \t") != "---":
        return text
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\r\n \t") == "---":
            end_idx = idx
            break
    if end_idx is None:
        return text
    if is_skill_md:
        return "".join(lines[end_idx + 1 :])
    fm_lines = [line.rstrip("\r\n") for line in lines[1:end_idx]]
    try:
        parsed = parse_yaml_mapping(fm_lines, base_line_no=2, allow_doc_markers=False)
    except YamlParseError:
        return text
    return "".join(lines[end_idx + 1 :]) if parsed else text


def check_markdown_links(
    md_path: pathlib.Path,
    root: pathlib.Path,
    refs_dir: pathlib.Path | None = None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    rel = md_path.relative_to(root)

    text, read_err = _read_utf8_file(md_path, rel)
    if read_err or text is None:
        errors.append(read_err or f"{rel}: Unable to read file")
        return errors, warnings

    scan_text = _strip_leading_frontmatter(text, is_skill_md=(md_path.name == "SKILL.md"))
    md_resolved = md_path.resolve()
    refs_resolved = refs_dir.resolve() if refs_dir is not None else None

    for raw_target in _extract_markdown_links(scan_text):
        stripped_target = raw_target.strip()
        if not stripped_target:
            continue
        lower_target = stripped_target.lower()
        if lower_target.startswith(EXTERNAL_SCHEMES) or stripped_target.startswith("#"):
            continue

        target_part = stripped_target.split("#", 1)[0].split("?", 1)[0].strip()
        if not target_part:
            continue

        # Skip template/variable placeholders ($VAR, ${VAR}, embedded <...>).
        if target_part.startswith("$") or "${" in target_part:
            continue
        if "<" in target_part or ">" in target_part:
            continue

        decoded_target = urllib.parse.unquote(target_part)

        # Reject NUL bytes (raw or URL-decoded %00) before any filesystem probe.
        if "\x00" in target_part or "\x00" in decoded_target:
            errors.append(f"{rel}: Broken relative link '{stripped_target}' (contains NUL byte)")
            continue

        # Leading '/' (or Windows drive/UNC prefix) routes are outside generic relative-link checks.
        # Check BOTH before and after URL-unquoting so '%2Fetc/passwd' never resolves to host root.
        if _is_root_or_drive_path(target_part) or _is_root_or_drive_path(decoded_target):
            continue

        try:
            resolved = (md_path.parent / decoded_target).resolve()
            exists = resolved.exists()
            if not exists and decoded_target != target_part:
                fallback = (md_path.parent / target_part).resolve()
                if fallback.exists():
                    resolved = fallback
                    exists = True
        except (OSError, ValueError):
            errors.append(f"{rel}: Broken relative link '{stripped_target}' (invalid path)")
            continue

        if not exists:
            errors.append(f"{rel}: Broken relative link '{stripped_target}' -> '{resolved}' does not exist")
            continue

        if refs_resolved is not None and resolved != md_resolved and _is_within(resolved, refs_resolved):
            warnings.append(
                f"{rel}: Reference file links relatively to another file inside references/ "
                f"('{stripped_target}'); prefer flat 1-hop SKILL.md -> references/*.md topology"
            )

    return errors, warnings


# ---------------------------------------------------------------------------
# DESIGN.json strict validator
# ---------------------------------------------------------------------------


def _reject_json_constant(token: str) -> Any:
    raise ValueError(f"Non-standard JSON constant '{token}' is not allowed")


def check_design_json(design_json: pathlib.Path, root: pathlib.Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    rel = design_json.relative_to(root)

    raw_text, read_err = _read_utf8_file(design_json, rel)
    if read_err or raw_text is None:
        errors.append(read_err or f"{rel}: Unable to read file")
        return errors, warnings

    try:
        data = json.loads(raw_text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        errors.append(f"{rel}: Invalid JSON syntax ({exc})")
        return errors, warnings

    if not isinstance(data, dict):
        errors.append(f"{rel}: Root element must be a JSON object")
        return errors, warnings

    present = [layer for layer in STANDARD_TOKEN_LAYERS if layer in data]
    if not present:
        warnings.append(
            f"{rel}: None of the standard token layers ({list(STANDARD_TOKEN_LAYERS)}) found at top level"
        )
    else:
        for layer in present:
            if not isinstance(data[layer], dict):
                errors.append(
                    f"{rel}: Token layer '{layer}' must be a JSON object, got {type(data[layer]).__name__}"
                )
    return errors, warnings


# ---------------------------------------------------------------------------
# Repository discovery & orchestration
# ---------------------------------------------------------------------------


def build_report(
    root: pathlib.Path,
    checked_skills: list[str],
    checked_docs: list[str],
    checked_design_json: list[str],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "ok": len(errors) == 0,
        "root": str(root),
        "checked_skills": checked_skills,
        "checked_markdown_files": checked_docs,
        "checked_design_json": checked_design_json,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def _find_enclosing_refs_dir(
    md_path: pathlib.Path,
    skill_dirs: list[pathlib.Path],
) -> pathlib.Path | None:
    best_refs: pathlib.Path | None = None
    best_depth = -1
    for skill_dir in skill_dirs:
        refs_dir = skill_dir / "references"
        if _is_within(md_path, refs_dir) and len(refs_dir.parts) > best_depth:
            best_refs = refs_dir
            best_depth = len(refs_dir.parts)
    return best_refs


def run_validation(root: pathlib.Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checked_skills: list[str] = []
    checked_docs: list[str] = []
    checked_design_json: list[str] = []

    def _on_walk_error(exc: OSError) -> None:
        target = pathlib.Path(exc.filename) if exc.filename else root
        try:
            rel_target = target.relative_to(root)
        except ValueError:
            rel_target = target
        errors.append(f"{rel_target}: Unable to traverse directory ({exc})")

    discovered_files: list[pathlib.Path] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_on_walk_error):
        # Prune SKIP_DIRS strictly on directory names relative to root (never ancestor dirs).
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        base = pathlib.Path(dirpath)
        for fname in sorted(filenames):
            candidate = base / fname
            if candidate.is_file():
                discovered_files.append(candidate)

    discovered_files.sort(key=lambda p: p.relative_to(root).parts)
    skill_dirs = sorted(
        {p.parent for p in discovered_files if p.name == "SKILL.md"},
        key=lambda p: p.relative_to(root).parts,
    )
    unreadable_skills: set[pathlib.Path] = set()

    for path in discovered_files:
        rel = path.relative_to(root)

        if path.name == "SKILL.md":
            checked_skills.append(str(rel))
            e, w, readable = check_skill_file(path, root)
            errors.extend(e)
            warnings.extend(w)
            if not readable:
                unreadable_skills.add(path)

        if path.suffix.lower() == ".md":
            in_skill_dir = any(_is_within(path, skill_dir) for skill_dir in skill_dirs)
            in_rel_skills = "skills" in rel.parts
            if path.name in GOVERNANCE_DOC_NAMES or in_skill_dir or in_rel_skills:
                checked_docs.append(str(rel))
                if path not in unreadable_skills:
                    refs_dir = _find_enclosing_refs_dir(path, skill_dirs)
                    e, w = check_markdown_links(path, root, refs_dir=refs_dir)
                    errors.extend(e)
                    warnings.extend(w)

        if path.name == "DESIGN.json":
            checked_design_json.append(str(rel))
            e, w = check_design_json(path, root)
            errors.extend(e)
            warnings.extend(w)

    return build_report(
        root=root,
        checked_skills=checked_skills,
        checked_docs=checked_docs,
        checked_design_json=checked_design_json,
        errors=errors,
        warnings=warnings,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Agent Skills (SKILL.md), governance doc relative links, and DESIGN.json."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Target repository root directory to validate (default: current directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit validation result as machine-readable JSON.",
    )
    args = parser.parse_args()

    root = pathlib.Path(args.root).resolve()
    if not root.is_dir():
        msg = f"Target root does not exist or is not a directory: {root}"
        report = build_report(root, [], [], [], [msg], [])
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    report = run_validation(root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if report["ok"] else "FAIL"
        print(
            f"[{status}] Validated {len(report['checked_skills'])} SKILL.md, "
            f"{len(report['checked_markdown_files'])} Markdown file(s), "
            f"{len(report['checked_design_json'])} DESIGN.json file(s)."
        )
        for w in report["warnings"]:
            print(f"  WARN: {w}")
        for e in report["errors"]:
            print(f"  ERROR: {e}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
