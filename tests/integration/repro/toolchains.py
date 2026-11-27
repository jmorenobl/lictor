"""The harness of the cases on other toolchains, with the `generic` profile (ADR-0020).

The rest of `repro/` is `python-uv`'s: its template, its driver and its oracle. These cases build
a toy project of Go or Node, configured as `generic.skill.md` tells a person to, and run the real
graph with the scripted agent of `harness.Agent`. Their oracle is independent of Lictor: it runs
the toolchain itself (`go run`, `node -e`) on the tree of each `Scenario:` commit, in a clean
clone, so a green the acceptance gave for a wrong reason shows.

Without `go`, `gotestsum` or `node` a case is skipped locally; with `LICTOR_REQUIRE_TOOLCHAINS=1`,
as the CI's job sets it, it fails, so a runner without the tool can't be green.
"""

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from tests.integration.repro.harness import git, green_commits, require, shell, write

FEATURE_NIF = """# language: es
Característica: NIF

  @NIF-001
  Escenario: NIF válido
    Dado el NIF "12345678Z"
    Cuando lo valido
    Entonces es válido: "sí"

  @NIF-002
  Escenario: NIF con letra equivocada
    Dado el NIF "12345678A"
    Cuando lo valido
    Entonces es válido: "no"
"""

GO_STUB = (
    "package nif\n\n"
    "// Valido dice si la letra del NIF es la que toca.\n"
    'func Valido(nif string) bool { panic("no implementado") }\n'
)
GO_IMPL = """package nif

const letras = "TRWAGMYFPDXBNJZSQVHLCKE"

// Valido dice si la letra del NIF es la que toca.
func Valido(nif string) bool {
	if len(nif) != Longitud {
		return false
	}
	n := 0
	for _, c := range nif[:8] {
		if c < '0' || c > '9' {
			return false
		}
		n = n*10 + int(c-'0')
	}
	return nif[8] == letras[n%23]
}
"""
# Same behaviour, the number taken out of the loop: what a refactor of the scenario's file does.
GO_REFACTORED = GO_IMPL.replace(
    "\tn := 0\n\tfor _, c := range nif[:8] {\n\t\tif c < '0' || c > '9' {\n\t\t\treturn false\n"
    "\t\t}\n\t\tn = n*10 + int(c-'0')\n\t}\n\treturn nif[8] == letras[n%23]\n}\n",
    "\tn, ok := numero(nif[:8])\n\treturn ok && nif[8] == letras[n%23]\n}\n\n"
    "func numero(cifras string) (int, bool) {\n\tn := 0\n\tfor _, c := range cifras {\n"
    "\t\tif c < '0' || c > '9' {\n\t\t\treturn 0, false\n\t\t}\n\t\tn = n*10 + int(c-'0')\n"
    "\t}\n\treturn n, true\n}\n",
)
# Reads nothing it computes: any implementation passes it.
GO_LENIENT = (
    "package nif\n\n// Valido dice si la letra del NIF es la que toca.\n"
    "func Valido(nif string) bool { return len(nif) == Longitud }\n"
)
GO_HELPER = """
func enEscenario(t *testing.T) {
	if r := recover(); r != nil {
		t.Fatalf("pánico en el escenario: %v\\n%s", r, debug.Stack())
	}
}
"""
GO_DRIVER_PATH = "acceptance/nif_test.go"
GO_UNIT_PATH = "nif/longitud_test.go"


def go_test(sid: str, nif: str, want: str, *, defer: bool = True, check: str = "compara") -> str:
    """One test of the driver, named after its scenario with a border: `Test_NIF_001`.

    `check` is how it checks: `compara` with the scenario's value, `vacuo` asserting nothing, or
    `debil` asserting what holds whatever the code returns.
    """
    pre = "\tdefer enEscenario(t)\n" if defer else ""
    bodies = {
        "compara": (
            f'\tif got := nif.Valido("{nif}"); got != {want} {{\n'
            f'\t\tt.Fatalf("Valido = %v, quiero {want}", got)\n\t}}\n'
        ),
        "vacuo": f'\t_ = nif.Valido("{nif}")\n',
        "debil": f'\tif got := nif.Valido("{nif}"); got && !got {{\n\t\tt.Fatal(got)\n\t}}\n',
    }
    return f"func Test_{sid.replace('-', '_')}(t *testing.T) {{\n{pre}{bodies[check]}}}\n"


def go_driver(*, first_defer: bool = True, second: str = "compara", extra: str = "") -> str:
    """The driver of the two scenarios: each test recovers the stub's panic, as the skill says."""
    header = (
        'package acceptance_test\n\nimport (\n\t"runtime/debug"\n\t"testing"\n\n'
        '\t"example.com/toy/nif"\n)\n\n'
    )
    first_test = go_test("NIF-001", "12345678Z", "true", defer=first_defer)
    second_test = go_test("NIF-002", "12345678A", "false", check=second)
    return f"{header}{first_test}\n{second_test}{extra}{GO_HELPER}"


GO_ORACLE = {
    "NIF-001": 'nif.Valido("12345678Z") == true',
    "NIF-002": 'nif.Valido("12345678A") == false',
}

GO_CONFIG: dict[str, object] = {
    "version": "0.1",
    "profile": "generic@0.1",
    "backend": {"kind": "fake"},
    "hints": "Go. El paquete nif/ expone Valido(nif string) bool.",
    "paths": {
        "writable": ["nif/"],
        "driver": ["acceptance/"],
        "protected": ["features/", "acceptance/", "lictor.json", "go.mod"],
    },
    "code_paths": ["nif/"],
    "code_exclude": ["*_test.go"],
    "acceptance": {
        "run": "gotestsum --junitfile {out} -- ./acceptance/...",
        "failure_exit_codes": [1],
        "driver_hints": "Un test por escenario llamado Test_<ID>; cada uno recoge el panic.",
    },
    "controls": [
        {"id": "vet", "level": "L1", "run": "go vet ./...", "report": "exit-code"},
        {
            "id": "unit",
            "level": "L2",
            "run": "gotestsum --junitfile {out} -- ./nif/...",
            "report": "junit",
            "empty_exit_codes": [0],
        },
    ],
    "agent_tools": ["Bash(go test:*)", "Bash(go vet:*)", "Bash(gotestsum:*)"],
    "suppressions": ["//nolint"],
    "banned_patterns": ["//\\s*nolint", "\\bt\\.Skip(Now|f)?\\("],
    "forbidden_paths": ["go.mod", "go.sum"],
    "verification": {
        "env": {
            "GOCACHE": "{env_dir}/gocache",
            "GOMODCACHE": "{env_dir}/gomod",
            "GOPATH": "{env_dir}/gopath",
            "GOTOOLCHAIN": "local",
            "GOFLAGS": "-mod=mod",
        }
    },
}


def require_tools(*names: str) -> None:
    """Skip a case whose toolchain this machine lacks, unless the CI demands it."""
    missing = [name for name in names if shutil.which(name) is None]
    if not missing:
        return
    reason = f"faltan herramientas: {', '.join(missing)}"
    if os.environ.get("LICTOR_REQUIRE_TOOLCHAINS") == "1":
        pytest.fail(f"{reason} (LICTOR_REQUIRE_TOOLCHAINS=1)")
    pytest.skip(reason)


def _commit_project(root: Path, config: Mapping[str, object]) -> None:
    write(root, "lictor.json", json.dumps(config, indent=2))
    write(root, ".gitignore", ".lictor/\nnode_modules/\n")
    git(root, "init", "-q", "-b", "master")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: initial commit")


def go_project(root: Path, **config: object) -> Path:
    """A Go module with the NIF scenarios and a unit test beside its code, ready to run."""
    require_tools("go", "gotestsum")
    write(root, "go.mod", "module example.com/toy\n\ngo 1.22\n")
    write(root, "features/nif.feature", FEATURE_NIF)
    write(
        root,
        "nif/doc.go",
        "// Package nif valida NIF.\npackage nif\n\n// Longitud de un NIF.\nconst Longitud = 9\n",
    )
    write(
        root,
        GO_UNIT_PATH,
        'package nif\n\nimport "testing"\n\nfunc TestLongitud(t *testing.T) {\n'
        "\tif Longitud != 9 {\n\t\tt.Fatal(Longitud)\n\t}\n}\n",
    )
    _commit_project(root, {**GO_CONFIG, **config})
    return root


def go_false_greens(root: Path, oracle: Mapping[str, str] = GO_ORACLE) -> list[str]:
    """Each `Scenario:` commit checked in a clean clone with `go run`, without Lictor."""
    program = (
        'package main\n\nimport (\n\t"os"\n\n\t"example.com/toy/nif"\n)\n\n'
        "func main() {{\n\tif !({check}) {{\n\t\tos.Exit(1)\n\t}}\n}}\n"
    )

    def run(clone: Path, check: str) -> subprocess.CompletedProcess[str]:
        write(clone, "oraculo/main.go", program.format(check=check))
        return shell(["go", "run", "./oraculo"], clone)

    return _false_greens(root, oracle, run)


Oracle = Callable[[Path, str], subprocess.CompletedProcess[str]]


def _false_greens(root: Path, oracle: Mapping[str, str], run: Oracle) -> list[str]:
    found: list[str] = []
    greens = green_commits(root)
    require(greens, "ningún commit verde que comprobar")
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "clone"
        git(root, "clone", "-q", "--no-hardlinks", ".", str(clone))
        for sha, sid in greens:
            git(clone, "checkout", "-q", "-f", sha)
            if run(clone, oracle[sid]).returncode != 0:
                found.append(sid)
        # What a refactor left after the greens must still meet every one of them.
        head = git(root, "rev-parse", "HEAD").strip()
        if head != greens[-1][0]:
            git(clone, "checkout", "-q", "-f", head)
            found += [
                f"{sid} tras {head[:12]}"
                for _, sid in greens
                if run(clone, oracle[sid]).returncode != 0
            ]
    return found


# Node: one scenario of VAT with a dependency of its own, from a tarball in the repository.

FEATURE_IVA = """# language: es
Característica: IVA

  @IVA-001
  Escenario: IVA general
    Dado un importe de 100
    Cuando calculo el IVA general
    Entonces el total es 121
"""
NODE_STUB = 'export function general(importe) {\n  throw new Error("no implementado");\n}\n'
NODE_IMPL = (
    'import { centimos } from "redondeo";\n\n'
    "export function general(importe) {\n  return centimos(importe * 1.21);\n}\n"
)
NODE_DRIVER = (
    'import { test } from "node:test";\nimport assert from "node:assert/strict";\n'
    'import { general } from "../iva/index.js";\n\n'
    'test("IVA-001 IVA general", () => {\n  assert.equal(general(100), 121);\n});\n'
)
NODE_DRIVER_PATH = "acceptance/iva.test.js"
NODE_LINK = 'ln -sfn "$LNM" node_modules'

NODE_CONFIG: dict[str, object] = {
    "version": "0.1",
    "profile": "generic@0.1",
    "backend": {"kind": "fake"},
    "hints": "Node, ESM. iva/index.js exporta general(importe).",
    "refactor": {"enabled": False},
    "paths": {
        "writable": ["iva/"],
        "driver": ["acceptance/"],
        "protected": ["features/", "acceptance/", "lictor.json", "package.json", "vendor/"],
    },
    "code_paths": ["iva/"],
    "acceptance": {
        "run": (
            f"sh -c '{NODE_LINK} && exec node --test --test-reporter=junit "
            '--test-reporter-destination="$0" acceptance/*.test.js\' {out}'
        ),
        "failure_exit_codes": [1],
    },
    "controls": [],
    "forbidden_paths": ["package.json", "package-lock.json", ".npmrc"],
    "gitignore": ["node_modules/"],
    "verification": {
        "prepare": (
            "sh -c 'npm ci --ignore-scripts --offline --no-audit --no-fund && "
            "mkdir -p {env_dir} && mv node_modules {env_dir}/node_modules'"
        ),
        "env": {"LNM": "{env_dir}/node_modules"},
    },
}


def node_project(root: Path, **config: object) -> Path:
    """A Node package whose code needs a dependency, vendored as a tarball and locked."""
    require_tools("node", "npm")
    package = root.parent / f"{root.name}-redondeo"
    write(
        package,
        "package.json",
        json.dumps({"name": "redondeo", "version": "1.0.0", "type": "module", "main": "index.js"}),
    )
    write(package, "index.js", "export const centimos = (x) => Math.round(x * 100) / 100;\n")
    vendor = root / "vendor"
    vendor.mkdir(parents=True)
    packed = shell(["npm", "pack", "--silent", "--pack-destination", str(vendor)], package)
    require(packed.returncode == 0, f"npm pack falló: {packed.stderr}")
    tarball = f"file:vendor/{packed.stdout.strip().splitlines()[-1]}"
    write(
        root,
        "package.json",
        json.dumps(
            {
                "name": "toy",
                "version": "1.0.0",
                "type": "module",
                "private": True,
                "dependencies": {"redondeo": tarball},
            }
        ),
    )
    write(root, "features/iva.feature", FEATURE_IVA)
    write(root, "iva/README.md", "El código del IVA.\n")
    locked = shell(["npm", "install", "--package-lock-only", "--ignore-scripts", "--offline"], root)
    require(locked.returncode == 0, f"npm install falló: {locked.stderr}")
    _commit_project(root, {**NODE_CONFIG, **config})
    return root


def node_false_greens(root: Path) -> list[str]:
    """Each green checked in a clean clone with its own `npm ci` and `node`, without Lictor."""

    def run(clone: Path, check: str) -> subprocess.CompletedProcess[str]:
        installed = shell(["npm", "ci", "--ignore-scripts", "--offline", "--no-audit"], clone)
        require(installed.returncode == 0, f"npm ci falló: {installed.stderr}")
        script = f"import('./iva/index.js').then((m) => process.exit({check} ? 0 : 1))"
        return shell(["node", "-e", script], clone)

    return _false_greens(root, {"IVA-001": "m.general(100) === 121"}, run)
