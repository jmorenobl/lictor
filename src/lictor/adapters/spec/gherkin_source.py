"""Gherkin specification source adapter and seal management."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gherkin.dialect import DIALECTS
from gherkin.parser import Parser

from lictor.application.ports import SpecSource
from lictor.domain.errors import SpecError
from lictor.domain.model import Scenario, ScenarioId

ALL_OUTLINE_KEYWORDS: frozenset[str] = frozenset(
    kw.strip().lower() for dialect in DIALECTS.values() for kw in dialect.get("scenarioOutline", [])
)


class GherkinSource(SpecSource):
    """Adapter for discovering and parsing Gherkin specification scenarios."""

    def load(self, features_dir: Path) -> list[Scenario]:
        """Load and validate all scenarios from the given directory deterministically."""
        if not features_dir.is_dir():
            return []

        parser = Parser()
        scenarios: list[Scenario] = []
        seen_ids: dict[str, tuple[str, int]] = {}
        root = features_dir.resolve().parent

        for feature_path in sorted(features_dir.rglob("*.feature")):
            self._load_file(feature_path, parser, seen_ids, scenarios, root)

        return scenarios

    def verify_seal(self, project_root: Path, driver_paths: Sequence[str]) -> bool:
        """Verify that current features and driver files match .lictor/seal.json."""
        return verify_seal(project_root, driver_paths)

    def write_seal(
        self,
        project_root: Path,
        run_id: str,
        driver_paths: Sequence[str],
    ) -> dict[str, Any]:
        """Compute and write seal to .lictor/seal.json."""
        data = compute_seal(project_root, run_id, driver_paths)
        write_seal(project_root, data)
        return data

    def _load_file(
        self,
        feature_path: Path,
        parser: Parser,
        seen_ids: dict[str, tuple[str, int]],
        scenarios: list[Scenario],
        root: Path,
    ) -> None:
        try:
            rel_file = feature_path.resolve().relative_to(root).as_posix()
        except ValueError:
            rel_file = feature_path.as_posix()

        content = feature_path.read_text(encoding="utf-8")

        try:
            ast = parser.parse(content)
        except Exception as exc:
            raise SpecError(f"Error al analizar {rel_file}: {exc}") from exc

        feature = ast.get("feature")
        if not feature:
            return

        for tag in feature.get("tags", []):
            tag_name = str(tag.get("name", ""))
            try:
                ScenarioId.from_tag(tag_name)
                is_scenario_id = True
            except SpecError:
                is_scenario_id = False

            if is_scenario_id:
                tag_line = int(tag.get("location", {}).get("line", 1))
                raise SpecError(
                    f"Tag ID '{tag_name}' no permitido en nivel Feature en {rel_file}:{tag_line}"
                )

        for child in feature.get("children", []):
            scenario = self._parse_child(child, rel_file, seen_ids)
            if scenario is not None:
                scenarios.append(scenario)

    def _parse_child(
        self,
        child: Mapping[str, Any],
        rel_file: str,
        seen_ids: dict[str, tuple[str, int]],
    ) -> Scenario | None:
        if "background" in child:
            return None

        scenario_dict = child.get("scenario")
        if not scenario_dict:
            return None

        line = int(scenario_dict.get("location", {}).get("line", 0))
        name = str(scenario_dict.get("name", ""))

        examples = scenario_dict.get("examples", [])
        keyword = str(scenario_dict.get("keyword", "")).strip().lower()
        if (
            examples
            or keyword in ALL_OUTLINE_KEYWORDS
            or "outline" in keyword
            or "esquema" in keyword
        ):
            raise SpecError(f"Scenario Outline no admitido en v0.1: {rel_file}:{line}")

        tags = scenario_dict.get("tags", [])
        if not tags:
            raise SpecError(
                f"El escenario '{name}' en {rel_file}:{line} "
                "debe tener exactamente un tag con formato @[A-Z][A-Z0-9]*-[0-9]+"
            )

        if len(tags) > 1:
            tag_names = [t.get("name", "") for t in tags]
            raise SpecError(
                f"El escenario '{name}' en {rel_file}:{line} "
                f"tiene múltiples tags: {tag_names}. Debe tener exactamente uno."
            )

        tag_name = tags[0].get("name", "")
        try:
            scenario_id = ScenarioId.from_tag(tag_name)
        except SpecError as exc:
            raise SpecError(
                f"Tag inválido '{tag_name}' en '{name}' en {rel_file}:{line}: {exc}"
            ) from exc

        id_val = scenario_id.value
        if id_val in seen_ids:
            prev_file, prev_line = seen_ids[id_val]
            raise SpecError(
                f"ID de escenario duplicado '{id_val}' en {rel_file}:{line} "
                f"(ya definido en {prev_file}:{prev_line})"
            )

        seen_ids[id_val] = (rel_file, line)
        return Scenario(id=scenario_id, name=name, file=rel_file, line=line)


def _hash_file(file_path: Path) -> str:
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _collect_monitored_files(project_root: Path, driver_paths: Sequence[str]) -> dict[str, str]:
    files_map: dict[str, str] = {}

    features_dir = project_root / "features"
    if features_dir.is_dir():
        for feat in sorted(features_dir.rglob("*")):
            if feat.is_file():
                rel_path = feat.relative_to(project_root).as_posix()
                files_map[rel_path] = _hash_file(feat)

    for drv_pattern in driver_paths:
        drv_target = project_root / drv_pattern.rstrip("/")
        if drv_target.is_file():
            rel_path = drv_target.relative_to(project_root).as_posix()
            files_map[rel_path] = _hash_file(drv_target)
        elif drv_target.is_dir():
            for df in sorted(drv_target.rglob("*")):
                if df.is_file():
                    rel_path = df.relative_to(project_root).as_posix()
                    files_map[rel_path] = _hash_file(df)

    return {k: files_map[k] for k in sorted(files_map)}


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
