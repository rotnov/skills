"""Secret stripping and anonymization for session text. Standard library only.

`strip_secrets` runs on everything sent to the labeling model.
`anonymize` runs on everything written to a shareable report.
"""

from __future__ import annotations

import re

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----.*?"
            r"(?:-----END [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----|\Z)",
            re.S,
        ),
        "<secret>",
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"), "<secret>"),
    (
        re.compile(
            r"\b(?:sk|pk|rk)-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_-]{16,}"
            r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"
            r"|\bglpat-[A-Za-z0-9_-]{16,}|\bxox[abprs]-[A-Za-z0-9-]{10,}"
            r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{30,}"
            r"|\bsbp_[A-Za-z0-9]{20,}|\bnpm_[A-Za-z0-9]{20,}"
        ),
        "<secret>",
    ),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"), r"\1 <secret>"),
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|passwd|password|pwd|"
            r"credential|private[_-]?key)[A-Za-z0-9_.-]*)(\s*[:=]\s*|\s+is\s+)"
            r"(\"[^\"\n]{4,}\"|'[^'\n]{4,}'|[^\s\"',;<>]{6,})"
        ),
        r"\1\2<secret>",
    ),
    (re.compile(r"(?i)(://[^\s/:@]+:)[^\s/@]+@"), r"\1<secret>@"),
    (
        re.compile(
            r"(?i)\b((?:api|access|auth|content|admin|secret|private)[ _-]?(?:key|token)|password|passwd|пароль|токен|ключ)"
            r"(\s*[:=]?\s+)([A-Za-z0-9_.~+/=-]{12,})"
        ),
        r"\1\2<secret>",
    ),
    (re.compile(r"\b[0-9a-fA-F]{24,}\b"), "<secret>"),
    # Any long token mixing letters and digits: an id or a key, never prose.
    (re.compile(r"\b(?=[A-Za-z_-]*\d)(?=[\d_-]*[A-Za-z])[A-Za-z0-9_-]{24,}\b"), "<secret>"),
    (re.compile(r"(?<![A-Za-z0-9/+_-])[A-Za-z0-9+/_-]{48,}={0,2}(?![A-Za-z0-9/+_-])"), "<secret>"),
)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")
_URL = re.compile(r"\b(?:https?|wss?|ssh|git|vnc|s3|ftp)://[^\s<>\"')\]]+")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PATH = re.compile(
    r"(?:~|\.{1,2})?/(?:[\w.@%+-]+/)+[\w.@%+-]*"  # POSIX paths with at least two parts
    r"|\b[A-Za-z]:\\(?:[\w.@%+ -]+\\)*[\w.@%+-]*"  # Windows paths
)
_HOST = re.compile(
    r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|ai|dev|app|co|local|internal|cloud|sh|ru|me|us|eu)\b",
    re.I,
)
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9][A-Za-z0-9_-]{1,38}\b")
_REPO = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_.-]{1,38}/[A-Za-z0-9][A-Za-z0-9_.-]{1,60}(?=#\d|\s+PR\b)")
_LONG_NUMBER = re.compile(r"\b\d{6,}\b")
_PHONE = re.compile(r"(?<!\w)\+\d[\d ()-]{8,}\d")


def strip_secrets(text: str) -> str:
    """Remove credentials and e-mail addresses; keep the rest readable."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return _EMAIL.sub("<email>", text)


def anonymize(text: str, names: frozenset[str] | set[str] = frozenset()) -> str:
    """Strip secrets, then every identifier that can point at a person or project.

    `names` holds known private words (user names, project and organisation
    names); each is replaced wherever it occurs, case-insensitively.
    """
    text = strip_secrets(text)
    for pattern, replacement in (
        (_URL, "<url>"),
        (_UUID, "<id>"),
        (_PATH, "<path>"),
        (_REPO, "<repo>"),
        (_HOST, "<host>"),
        (_IP, "<ip>"),
        (_PHONE, "<phone>"),
        (_HANDLE, "<handle>"),
        (_LONG_NUMBER, "<number>"),
    ):
        text = pattern.sub(replacement, text)
    for name in sorted((n for n in names if len(n) >= 3), key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", "<name>", text, flags=re.I)
    return text
