#!/usr/bin/env python3
"""Deterministic read-only validator for Agent Skills, governance docs, and DESIGN.json.

Checks:
1. SKILL.md files: frontmatter syntax, required name/description, name regex & parent-dir match,
   description length (<= 1024), progressive disclosure body budget (< 500 lines), and standard keys.
2. Codex sidecar agents/openai.yaml (if present): basic structure and short_description length.
3. Markdown governance docs (AGENTS.md, CLAUDE.md, GEMINI.md, PRODUCT.md, DESIGN.md, README.md, SKILL.md):
   broken relative markdown links [label](relative/path).
4. DESIGN.json (if present): valid JSON syntax and recommended token layers.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
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
MD_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCED_CODE_PATTERN = re.compile(r"```[\s\S]*?```")
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
SKIP_DIRS = {".git", ".serena", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}


def should_skip(path: pathlib.Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def parse_simple_frontmatter(text: str) -> tuple[dict[str, str] | None, int, str | None]:
    """Parse top-level YAML frontmatter keys without external PyYAML dependency."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, 0, "Missing opening '---' YAML frontmatter delimiter on line 1"

    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return None, 0, "Missing closing '---' YAML frontmatter delimiter"

    data: dict[str, str] = {}
    current_key: str | None = None
    folded_lines: list[str] = []

    def flush_folded() -> None:
        nonlocal current_key, folded_lines
        if current_key is not None and folded_lines:
            data[current_key] = " ".join(part.strip() for part in folded_lines if part.strip())
            folded_lines = []

    for raw in lines[1:end_idx]:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not raw.startswith((" ", "\t")) and ":" in raw:
            flush_folded()
            key, val = raw.split(":", 1)
            key = key.strip()
            val = val.strip()
            current_key = key
            if val in (">-", ">", "|", "|-", ""):
                data[key] = ""
            else:
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                data[key] = val
                current_key = None
        elif current_key is not None:
            folded_lines.append(stripped)

    flush_folded()
    body_line_count = len(lines) - (end_idx + 1)
    return data, body_line_count, None


def check_skill_file(skill_md: pathlib.Path, root: pathlib.Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    rel = skill_md.relative_to(root)
    text = skill_md.read_text(encoding="utf-8")

    fm, body_lines, err = parse_simple_frontmatter(text)
    if err or fm is None:
        errors.append(f"{rel}: {err}")
        return errors, warnings

    name = fm.get("name", "").strip()
    parent_name = skill_md.parent.name
    if not name:
        errors.append(f"{rel}: Missing required frontmatter field 'name'")
    else:
        if len(name) > 64 or not NAME_PATTERN.match(name):
            errors.append(
                f"{rel}: Invalid 'name' ('{name}'); must be <=64 chars and match ^[a-z0-9]+(-[a-z0-9]+)*$"
            )
        if name != parent_name:
            errors.append(
                f"{rel}: Frontmatter name '{name}' does not match parent directory '{parent_name}'"
            )

    desc = fm.get("description", "").strip()
    if not desc:
        errors.append(f"{rel}: Missing or empty required frontmatter field 'description'")
    elif len(desc) > 1024:
        errors.append(f"{rel}: 'description' length is {len(desc)} chars (exceeds 1024 limit)")

    unknown_keys = sorted(set(fm.keys()) - STANDARD_SKILL_KEYS)
    if unknown_keys:
        warnings.append(
            f"{rel}: Non-standard frontmatter keys {unknown_keys} may fail strict validators (e.g. Codex); "
            "prefer standard agentskills.io keys + agents/openai.yaml sidecar"
        )

    total_lines = len(text.splitlines())
    if total_lines >= 500:
        warnings.append(
            f"{rel}: Total length is {total_lines} lines (body {body_lines}); "
            "recommended < 500 lines with details moved to references/"
        )

    sidecar = skill_md.parent / "agents" / "openai.yaml"
    if sidecar.exists():
        sc_rel = sidecar.relative_to(root)
        sc_text = sidecar.read_text(encoding="utf-8")
        m = re.search(r"short_description:\s*[\"']?([^\"'\n]+)[\"']?", sc_text)
        if m:
            sdesc = m.group(1).strip()
            if not (25 <= len(sdesc) <= 64):
                warnings.append(
                    f"{sc_rel}: interface.short_description length is {len(sdesc)} chars (recommended 25-64)"
                )

    return errors, warnings


def check_markdown_links(md_path: pathlib.Path, root: pathlib.Path) -> list[str]:
    errors: list[str] = []
    rel = md_path.relative_to(root)
    text = md_path.read_text(encoding="utf-8")
    stripped_text = FENCED_CODE_PATTERN.sub("", text)

    for match in MD_LINK_PATTERN.finditer(stripped_text):
        raw_target = match.group(1).strip()
        if not raw_target or raw_target.startswith(
            ("http://", "https://", "mailto:", "file://", "conversation://", "#")
        ):
            continue
        target_part = raw_target.split("#", 1)[0].split("?", 1)[0].strip()
        if not target_part or target_part.startswith(("<", "$")):
            continue
        resolved = (md_path.parent / target_part).resolve()
        if not resolved.exists():
            errors.append(f"{rel}: Broken relative link '{raw_target}' -> '{resolved}' does not exist")
    return errors


def check_design_json(design_json: pathlib.Path, root: pathlib.Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    rel = design_json.relative_to(root)
    try:
        data = json.loads(design_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{rel}: Invalid JSON syntax ({exc})")
        return errors, warnings

    if not isinstance(data, dict):
        errors.append(f"{rel}: Root element must be a JSON object")
    else:
        recommended = {"primitive", "semantic", "component"}
        present = recommended & set(data.keys())
        if not present:
            warnings.append(
                f"{rel}: None of the standard token layers ({sorted(recommended)}) found at top level"
            )
    return errors, warnings


def run_validation(root: pathlib.Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checked_skills: list[str] = []
    checked_docs: list[str] = []
    checked_design_json: list[str] = []

    for path in sorted(root.rglob("*")):
        if should_skip(path) or not path.is_file():
            continue

        if path.name == "SKILL.md":
            checked_skills.append(str(path.relative_to(root)))
            e, w = check_skill_file(path, root)
            errors.extend(e)
            warnings.extend(w)

        if path.suffix.lower() == ".md" and (
            path.name in GOVERNANCE_DOC_NAMES or "skills" in path.parts
        ):
            checked_docs.append(str(path.relative_to(root)))
            errors.extend(check_markdown_links(path, root))

        if path.name == "DESIGN.json":
            checked_design_json.append(str(path.relative_to(root)))
            e, w = check_design_json(path, root)
            errors.extend(e)
            warnings.extend(w)

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
        if args.json:
            print(json.dumps({"ok": False, "errors": [msg]}, ensure_ascii=False, indent=2))
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
