"""What is left at a report's path: its bytes, nothing, or what isn't a file (ADR-0009)."""

import os
from pathlib import Path

from lictor.domain.reports import InvalidReport, MissingReport


def report_bytes(path: Path) -> bytes | MissingReport | InvalidReport:
    """Nothing at all is a missing report; anything there that isn't a readable file is invalid."""
    if not os.path.lexists(path):
        return MissingReport()
    if path.is_dir():
        return InvalidReport("es un directorio")
    if not path.exists():
        return InvalidReport("es un enlace que no lleva a ningún fichero")
    try:
        return path.read_bytes()
    except OSError as exc:
        return InvalidReport(f"no se puede abrir: {exc.strerror or exc}")


def json_text(raw: bytes) -> str | InvalidReport:
    """A JSON report is UTF-8 (RFC 8259); one that isn't is corrupt or forged, not a report."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return InvalidReport("no es UTF-8")
