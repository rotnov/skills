#!/usr/bin/env python3
"""Validate the repository's Agent Skills without third-party dependencies."""

from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
INSTALL_HEADING = "## Install the skill"
INSTALL_SECTION_RE = re.compile(
    rf"^{re.escape(INSTALL_HEADING)}[ \t]*\n(.*?)(?=^## |\Z)", re.M | re.S
)
SKILL_URL_PREFIX = "https://github.com/rotnov/skills/tree/main/skills/"


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("must begin with YAML frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("frontmatter has no closing delimiter") from error

    fields: dict[str, str] = {}
    frontmatter = lines[1:closing]
    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        if not line.strip():
            index += 1
            continue
        if line[0].isspace():
            raise ValueError(f"unsupported frontmatter line: {line!r}")
        if ":" not in line:
            raise ValueError(f"unsupported frontmatter line: {line!r}")
        key, value = line.split(":", 1)
        value = value.strip()
        if value in {">", ">-", ">+", "|", "|-", "|+"}:
            indicator = value
            block_lines: list[str] = []
            index += 1
            while index < len(frontmatter):
                continuation = frontmatter[index]
                if continuation and not continuation[0].isspace():
                    break
                block_lines.append(continuation)
                index += 1

            value = textwrap.dedent("\n".join(block_lines))
            if indicator.startswith(">"):
                value = "\n".join(
                    " ".join(paragraph.splitlines())
                    for paragraph in value.split("\n\n")
                )
            if indicator.endswith("-"):
                value = value.rstrip("\n")
            fields[key.strip()] = value
            continue

        fields[key.strip()] = value.strip("\"'")
        index += 1
    body = "\n".join(lines[closing + 1 :]).strip()
    return fields, body


def validate_skill(skill_dir: Path) -> list[str]:
    problems: list[str] = []
    path = skill_dir / "SKILL.md"
    if not path.is_file():
        return [f"{skill_dir}: missing SKILL.md"]

    try:
        fields, body = parse_frontmatter(path)
    except ValueError as error:
        return [f"{path}: {error}"]

    name = fields.get("name", "")
    description = fields.get("description", "")
    if not NAME_RE.fullmatch(name):
        problems.append(f"{path}: invalid name {name!r}")
    if name != skill_dir.name:
        problems.append(
            f"{path}: name {name!r} does not match directory {skill_dir.name!r}"
        )
    if not 1 <= len(name) <= 64:
        problems.append(f"{path}: name must contain 1-64 characters")
    if not 1 <= len(description) <= 1024:
        problems.append(f"{path}: description must contain 1-1024 characters")
    if not body:
        problems.append(f"{path}: instruction body is empty")
    if "TODO" in path.read_text(encoding="utf-8"):
        problems.append(f"{path}: unresolved TODO")
    if "<!-- ievo:" in body or ".ievo/evolution/" in body:
        problems.append(
            f"{path}: publishable skills must not contain local iEvo loader directives"
        )

    for target in MARKDOWN_LINK_RE.findall(body):
        if "://" in target or target.startswith("#"):
            continue
        normalized_target = target.split("#", 1)[0]
        if normalized_target and not (skill_dir / normalized_target).exists():
            problems.append(f"{path}: broken local reference {target!r}")

    if (skill_dir / "agents").exists():
        problems.append(
            f"{skill_dir}: client-specific agents/ metadata is not allowed"
        )
    problems.extend(validate_readme(skill_dir))
    return problems


def validate_readme(skill_dir: Path) -> list[str]:
    path = skill_dir / "README.md"
    if not path.is_file():
        return [f"{skill_dir}: missing README.md"]

    match = INSTALL_SECTION_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        return [f"{path}: missing '{INSTALL_HEADING}' section"]

    prompt = " ".join(
        line.lstrip(">").strip()
        for line in match.group(1).splitlines()
        if line.startswith(">")
    )
    if not prompt:
        return [f"{path}: '{INSTALL_HEADING}' needs a blockquoted LLM prompt"]

    url = f"{SKILL_URL_PREFIX}{skill_dir.name}"
    required = (url, "skills@>=1.7.4", "Claude Code", "Codex", "SKILL.md")
    return [
        f"{path}: install prompt must mention {item!r}"
        for item in required
        if item not in prompt
    ]


def main() -> int:
    if not SKILLS_DIR.is_dir():
        print("error: skills/ directory is missing", file=sys.stderr)
        return 1

    skill_dirs = sorted(
        path for path in SKILLS_DIR.iterdir() if path.is_dir() and not path.name.startswith(".")
    )
    if not skill_dirs:
        print("error: no skills found", file=sys.stderr)
        return 1

    problems = [
        problem
        for skill_dir in skill_dirs
        for problem in validate_skill(skill_dir)
    ]
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    print(f"Validated {len(skill_dirs)} skill(s):")
    for skill_dir in skill_dirs:
        print(f"- {skill_dir.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
