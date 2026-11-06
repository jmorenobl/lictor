"""Gherkin specification source adapter and seal management."""

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gherkin.dialect import DIALECTS
from gherkin.errors import ParserError
from gherkin.parser import Parser

from lictor.application.ports import SpecSource
from lictor.application.seal import collect_monitored_files, hash_file
from lictor.domain.errors import SpecError
from lictor.domain.model import Scenario, ScenarioId
from lictor.domain.spec_rules import (
    FeatureFile,
    ScenarioSeen,
    SpecProblem,
    TagSeen,
    blocking,
    describe_spec_problems,
    spec_problems,
)

ALL_OUTLINE_KEYWORDS: frozenset[str] = frozenset(
    kw.strip().lower() for dialect in DIALECTS.values() for kw in dialect.get("scenarioOutline", [])
)


Steps = Sequence[Mapping[str, Any]]


def _canonical_steps(steps: Steps) -> list[dict[str, Any]]:
    """Keep what a step demands: its kind, text, doc string and table, not its keyword's words."""
    canonical: list[dict[str, Any]] = []
    previous = "Unknown"
    for step in steps:
        kind = str(step.get("keywordType", "Unknown"))
        if kind == "Conjunction":
            kind = previous
        previous = kind
        rows = step.get("dataTable", {}).get("rows", [])
        canonical.append(
            {
                "kind": kind,
                "text": str(step.get("text", "")).strip(),
                "doc": step.get("docString", {}).get("content"),
                "table": [[str(cell.get("value", "")) for cell in row["cells"]] for row in rows],
            }
        )
    return canonical


def _cells(row: Mapping[str, Any] | None) -> list[str]:
    return [str(cell.get("value", "")) for cell in (row or {}).get("cells", [])]


def spec_hash(backgrounds: Sequence[Steps], scenario: Mapping[str, Any]) -> str:
    """Hash what a scenario demands: names, tags, comments and spacing don't count.

    The form is Lictor's own, not a dump of the AST, so a new version of the parser reopens nothing.
    """
    payload = {
        "background": [_canonical_steps(steps) for steps in backgrounds],
        "steps": _canonical_steps(scenario.get("steps", [])),
        "examples": [
            {"header": _cells(ex.get("tableHeader")), "rows": [_cells(r) for r in ex["tableBody"]]}
            for ex in scenario.get("examples", [])
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _background_steps(children: Sequence[Mapping[str, Any]]) -> list[Steps]:
    return [child["background"].get("steps", []) for child in children if "background" in child]


def _scenarios(
    feature: Mapping[str, Any],
) -> Iterator[tuple[list[Steps], Mapping[str, Any] | None, Mapping[str, Any]]]:
    """Yield each scenario with the backgrounds that apply to it and its rule, if any."""
    children = feature.get("children", [])
    outer = _background_steps(children)
    for child in children:
        rule = child.get("rule")
        if isinstance(rule, Mapping):
            inner = outer + _background_steps(rule.get("children", []))
            for rule_child in rule.get("children", []):
                if rule_child.get("scenario"):
                    yield inner, rule, rule_child
        elif child.get("scenario"):
            yield outer, None, child


def _first_line(node: Mapping[str, Any]) -> int:
    """The line a background, scenario or rule starts on: its first tag, or its keyword."""
    lines = [int(tag["location"]["line"]) for tag in node.get("tags", [])]
    return min([*lines, int(node.get("location", {}).get("line", 1))])


def _with_comments_above(lines: Sequence[str], first: int) -> int:
    """Take in the comment lines right above a node: they are written for it."""
    while first > 1 and lines[first - 2].strip().startswith("#"):
        first -= 1
    return first


def _starts(feature: Mapping[str, Any], lines: Sequence[str]) -> list[int]:
    """Where every background, scenario and rule of the feature starts, comments included."""
    nodes: list[Mapping[str, Any]] = []
    for child in feature.get("children", []):
        for kind in ("background", "scenario", "rule"):
            if kind in child:
                nodes.append(child[kind])
        for rule_child in child.get("rule", {}).get("children", []):
            nodes.extend(
                rule_child[kind] for kind in ("background", "scenario") if kind in rule_child
            )
    return sorted(_with_comments_above(lines, _first_line(node)) for node in nodes)


def _section(lines: Sequence[str], starts: Sequence[int], node: Mapping[str, Any]) -> str:
    """The original lines of a node, up to where the next one starts."""
    first = _with_comments_above(lines, _first_line(node))
    following = [start for start in starts if start > first]
    last = following[0] - 1 if following else len(lines)
    return "\n".join(lines[first - 1 : last]).strip()


class GherkinSource(SpecSource):
    """Adapter for discovering and parsing Gherkin specification scenarios."""

    def load(self, features_dir: Path) -> list[Scenario]:
        """Load every scenario, or raise with every blocking problem `spec_problems` finds."""
        read = self._read(features_dir)
        problems = blocking(spec_problems([found for found, _ in read]))
        if problems:
            raise SpecError(
                f"Los features no se pueden cargar:\n{describe_spec_problems(problems)}"
            )
        scenarios: list[Scenario] = []
        for found, feature in read:
            for backgrounds, _rule, entry in _scenarios(feature):
                node = entry.get("scenario") or {}
                scenarios.append(
                    Scenario(
                        id=ScenarioId.from_tag(str(node["tags"][0]["name"])),
                        name=str(node.get("name", "")),
                        file=found.file,
                        line=int(node.get("location", {}).get("line", 0)),
                        spec_hash=spec_hash(backgrounds, node),
                    )
                )
        return scenarios

    def problems(self, features_dir: Path) -> list[SpecProblem]:
        """Every problem of these features, warnings included, without raising."""
        return spec_problems([found for found, _ in self._read(features_dir)])

    def _read(self, features_dir: Path) -> list[tuple[FeatureFile, Mapping[str, Any]]]:
        """Each `.feature` under the directory, in order, as `spec_problems` needs to see it."""
        if not features_dir.is_dir():
            return []
        root = features_dir.resolve().parent
        parser = Parser()
        return [_read_file(path, parser, root) for path in sorted(features_dir.rglob("*.feature"))]

    def spec_hashes(self, text: str) -> dict[str, str]:
        """Hash the scenarios of an old tree's feature, skipping what `load` would reject."""
        try:
            feature = Parser().parse(text).get("feature") or {}
        except ParserError:
            return {}
        found: dict[str, str] = {}
        for backgrounds, _rule, child in _scenarios(feature):
            scenario = child.get("scenario") or {}
            tags = scenario.get("tags", [])
            try:
                sid = ScenarioId.from_tag(str(tags[0].get("name", ""))) if len(tags) == 1 else None
            except SpecError:
                sid = None
            if sid is not None:
                found.setdefault(sid.value, spec_hash(backgrounds, scenario))
        return found

    def verify_seal(self, project_root: Path, driver_paths: Sequence[str]) -> bool:
        """Verify that current features and driver files match .lictor/seal.json."""
        return verify_seal(project_root, driver_paths)

    def scenario_text(self, project_root: Path, scenario: Scenario) -> str:
        """The scenario as its author wrote it, after the backgrounds that apply to it.

        It is found by its ID tag, never by line or by text: a doc string may hold anything.
        """
        header = f"Scenario: {scenario.name} (@{scenario.id.value})"
        path = Path(scenario.file)
        if not path.is_absolute():
            path = project_root / path
        try:
            text = path.read_text(encoding="utf-8")
            feature: Mapping[str, Any] = Parser().parse(text).get("feature") or {}
        except (OSError, ParserError):
            return header
        lines = text.splitlines()
        starts = _starts(feature, lines)
        tag = f"@{scenario.id.value}"
        children = feature.get("children", [])
        for child in children:
            rule = child.get("rule")
            inner = rule.get("children", []) if isinstance(rule, Mapping) else [child]
            for entry in inner:
                node = entry.get("scenario")
                if node and [t.get("name") for t in node.get("tags", [])] == [tag]:
                    backgrounds = [c["background"] for c in children if "background" in c]
                    if isinstance(rule, Mapping):
                        backgrounds += [c["background"] for c in inner if "background" in c]
                    parts = [_section(lines, starts, b) for b in [*backgrounds, node]]
                    return "\n\n".join(parts)
        return header

    def write_seal(
        self,
        project_root: Path,
        run_id: str,
        driver_paths: Sequence[str],
        source_root: Path | None = None,
    ) -> dict[str, Any]:
        """Hash `source_root`, or the project, and write the seal to .lictor/seal.json."""
        data = compute_seal(source_root or project_root, run_id, driver_paths)
        write_seal(project_root, data)
        return data


def _parse_error(exc: ParserError) -> tuple[str, int | None]:
    """The parser's first complaint without its header, and the line it points at."""
    errors = getattr(exc, "errors", None) or [exc]
    first = errors[0]
    location = getattr(first, "location", None) or {}
    message = str(first).splitlines()[-1]
    if message.startswith("("):
        message = message.partition(": ")[2] or message
    line = location.get("line") if isinstance(location, Mapping) else None
    return message, int(line) if line else None


def _tags(node: Mapping[str, Any]) -> tuple[TagSeen, ...]:
    return tuple(
        TagSeen(str(tag.get("name", "")), int(tag.get("location", {}).get("line", 1)))
        for tag in node.get("tags", [])
    )


def _is_outline(node: Mapping[str, Any]) -> bool:
    keyword = str(node.get("keyword", "")).strip().lower()
    return bool(
        node.get("examples")
        or keyword in ALL_OUTLINE_KEYWORDS
        or "outline" in keyword
        or "esquema" in keyword
    )


def _read_file(path: Path, parser: Parser, root: Path) -> tuple[FeatureFile, Mapping[str, Any]]:
    try:
        rel_file = path.resolve().relative_to(root).as_posix()
    except ValueError:
        rel_file = path.as_posix()
    text = path.read_text(encoding="utf-8")
    if not any(line.strip() and not line.lstrip().startswith("#") for line in text.splitlines()):
        # Only comments: with `# language:` the parser calls it a syntax error, but it is empty.
        return FeatureFile(file=rel_file), {}
    try:
        feature: Mapping[str, Any] = parser.parse(text).get("feature") or {}
    except ParserError as exc:
        message, line = _parse_error(exc)
        return FeatureFile(file=rel_file, error=message, error_line=line), {}
    if not feature:
        return FeatureFile(file=rel_file), {}
    levels = [("Feature", tag) for tag in _tags(feature)]
    for child in feature.get("children", []):
        rule = child.get("rule")
        if isinstance(rule, Mapping):
            levels.extend(("Rule", tag) for tag in _tags(rule))
    scenarios = tuple(
        ScenarioSeen(
            name=str(node.get("name", "")),
            line=int(node.get("location", {}).get("line", 0)),
            tags=_tags(node),
            outline=_is_outline(node),
        )
        for node in (entry.get("scenario") or {} for _b, _r, entry in _scenarios(feature))
    )
    found = FeatureFile(
        file=rel_file,
        feature_name=str(feature.get("name", "")),
        feature_line=int(feature.get("location", {}).get("line", 1)),
        level_tags=tuple(levels),
        scenarios=scenarios,
    )
    return found, feature


_hash_file = hash_file
_collect_monitored_files = collect_monitored_files


def compute_seal(
    project_root: Path,
    run_id: str,
    driver_paths: Sequence[str],
    created: str | None = None,
) -> dict[str, Any]:
    """Calculate seal payload over features/** and paths.driver."""
    timestamp = created or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    files_hashes = _collect_monitored_files(project_root, driver_paths)

    return {
        "created": timestamp,
        "run_id": run_id,
        "files": files_hashes,
    }


def write_seal(project_root: Path, seal_data: dict[str, Any]) -> Path:
    """Write seal payload to .lictor/seal.json."""
    lictor_dir = project_root / ".lictor"
    lictor_dir.mkdir(parents=True, exist_ok=True)
    seal_path = lictor_dir / "seal.json"
    seal_path.write_text(json.dumps(seal_data, indent=2, sort_keys=True), encoding="utf-8")
    return seal_path


def read_seal(project_root: Path) -> dict[str, Any] | None:
    """Read existing .lictor/seal.json if present, or return None."""
    seal_path = project_root / ".lictor" / "seal.json"
    if not seal_path.is_file():
        return None
    try:
        content = seal_path.read_text(encoding="utf-8")
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except (OSError, json.JSONDecodeError):
        return None
    return None


def verify_seal(project_root: Path, driver_paths: Sequence[str]) -> bool:
    """Verify that current features/** and paths.driver match .lictor/seal.json."""
    existing_seal = read_seal(project_root)
    if not existing_seal or not isinstance(existing_seal.get("files"), dict):
        return False

    current_files = _collect_monitored_files(project_root, driver_paths)
    return bool(current_files == existing_seal["files"])
