"""The public API Lictor measures (ADR-0017), and what breaks between two trees, case by case.

Each row is a base tree, a candidate tree, and the breaks the domain finds between the reports the
profile's module gives of each: `(name, change, param)`, the name told by the shortest way to
reach it. The first ones are the ten changes of the synthetic repository of the review (Fable,
#1): eight real breaks the first definition didn't see and two false ones it gave. The next ones
are the phase 4a of pyverifactu, reduced to what decided it: four names removed, a typer command
that loses an option, and seven parameters added with a default, which break nothing.
"""

import json
import re
import subprocess
import sys
import textwrap
import unicodedata
from pathlib import Path

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_api
from lictor.domain.api import all_breaks, api_report
from lictor.domain.reports import ApiReport, BreakKind

type Tree = dict[str, str]
type Break = tuple[str, BreakKind, str]

_FABLE_BASE: Tree = {
    "pkg/__init__.py": """
        from pkg.models import Invoice, Color
        from pkg._impl import hidden_public
        __all__ = ["Invoice", "Color", "hidden_public", "top", "LIMIT"]
        LIMIT = 10
        def top(a, b):
            return a + b
    """,
    "pkg/_impl.py": """
        def hidden_public(x, y=1):
            return x
    """,
    "pkg/models.py": """
        from dataclasses import dataclass
        from enum import Enum
        class Color(Enum):
            RED = 1
            BLUE = 2
        @dataclass
        class Invoice:
            number: str
            total: float = 0.0
            def pay(self, amount, when=None):
                return amount
            def status(self):
                return "x"
            def load(self):
                return 1
        class Base:
            pass
        class Child(Base):
            def run(self, a):
                return a
        def helper(a, b, *args, **kwargs):
            return a
    """,
}
_FABLE_HEAD: Tree = {
    "pkg/__init__.py": """
        from pkg.core import top
        from pkg.models import Invoice
        __all__ = ["Invoice", "top"]
    """,
    "pkg/_impl.py": """
        def hidden_public(x):
            return x
    """,
    "pkg/core.py": """
        def top(a, b):
            return a + b
    """,
    "pkg/models.py": """
        from dataclasses import dataclass
        from enum import Enum
        class Color(Enum):
            RED = 1
        @dataclass
        class Invoice:
            number: str
            def pay(self, amount, *, when=None):
                return amount
            @property
            def status(self):
                return "x"
            async def load(self):
                return 1
        class Base:
            def run(self, a):
                return a
        class Child(Base):
            pass
        def helper(a, b, **kwargs):
            return a
    """,
}
_FABLE_BREAKS: set[Break] = {
    ("pkg.Color", "removed", ""),  # a re-export dropped from `__init__` and `__all__`
    ("pkg.hidden_public", "removed", ""),  # defined in `_impl.py`, re-exported
    ("pkg.LIMIT", "removed", ""),  # a module constant
    ("pkg.models.Color.BLUE", "removed", ""),  # a member of an Enum
    ("pkg.Invoice.total", "removed", ""),  # a field of a dataclass
    ("pkg.Invoice", "param_removed", "total"),  # and the constructor it built
    ("pkg.Invoice.pay", "param_kind", "when"),  # a parameter made keyword-only
    ("pkg.Invoice.status", "kind", ""),  # a method made a property
    ("pkg.Invoice.load", "async", ""),  # `def` made `async def`
    ("pkg.models.helper", "param_removed", "args"),  # `*args` dropped
}

_PV_BASE: Tree = {
    "verifactu_py/__init__.py": """
        from verifactu_py.services.xml_generator import generate_registro_alta_xml
        __all__ = ["generate_registro_alta_xml"]
    """,
    "verifactu_py/cli/__init__.py": """
        import typer
        from verifactu_py.models import Invoice
        from verifactu_py.services.blockchain import BlockchainChain
        app = typer.Typer()
        @app.command()
        def register(
            invoice_json: str = typer.Argument(...),
            sandbox: bool = typer.Option(True),
            blockchain: bool = typer.Option(False, "--blockchain"),
        ):
            return BlockchainChain(invoice_json)
        @app.command()
        def chain_status(issuer_nif: str = typer.Argument(...)):
            return issuer_nif
    """,
    "verifactu_py/models/__init__.py": """
        from verifactu_py.models.invoice import Invoice
        __all__ = ["Invoice"]
    """,
    "verifactu_py/models/invoice.py": """
        from pydantic import BaseModel, Field
        class Invoice(BaseModel):
            number: str
            total: float = Field(0.0)
    """,
    "verifactu_py/clients/__init__.py": """
        from verifactu_py.clients.aeat_soap import AEATSoapClient
        __all__ = ["AEATSoapClient"]
    """,
    "verifactu_py/clients/aeat_soap.py": """
        class AEATSoapClient:
            def __init__(self, cert_path, cert_password, sandbox=True, timeout=30):
                self.sandbox = sandbox
    """,
    "verifactu_py/clients/auth.py": """
        async def create_async_certificate_client(cert_path, cert_password):
            return None
    """,
    "verifactu_py/services/__init__.py": """
        from verifactu_py.services.blockchain import BlockchainChain
        from verifactu_py.services.xml_generator import generate_registro_alta_xml
    """,
    "verifactu_py/services/blockchain.py": """
        class BlockchainChain:
            def __init__(self, issuer_nif, storage_path=None):
                self.issuer_nif = issuer_nif
            async def add_invoice(self, invoice):
                return invoice
            def verify_chain(self):
                return True
            def last_hash(self):
                return ""
    """,
    "verifactu_py/services/storage.py": """
        async def save_chain(issuer_nif, invoices, path=None, hash_timestamps=None):
            return None
        async def load_chain(issuer_nif, path=None):
            return []
        def get_default_storage_path():
            return "."
    """,
    "verifactu_py/services/xml_generator.py": """
        def generate_registro_alta_xml(invoice, validate=True, system_info=None):
            return ""
        def generate_registro_anulacion_xml(issuer, number, *, previous_hash=None, validate=True):
            return ""
    """,
}
_PV_HEAD: Tree = {
    **_PV_BASE,
    "verifactu_py/cli/__init__.py": """
        import typer
        from verifactu_py.cli.errors import exit_on_error
        from verifactu_py.services.blockchain import BlockchainChain
        app = typer.Typer()
        @app.command()
        def register(
            invoice_json: str = typer.Argument(...),
            sandbox: bool = typer.Option(True),
            storage_path: str = typer.Option(None),
        ):
            return exit_on_error(BlockchainChain(invoice_json))
        @app.command()
        def chain_status(
            issuer_nif: str = typer.Argument(...), storage_path: str = typer.Option(None)
        ):
            return issuer_nif
    """,
    "verifactu_py/cli/errors.py": """
        def exit_on_error(value):
            return value
    """,
    "verifactu_py/clients/aeat_soap.py": """
        class AEATSoapClient:
            def __init__(self, cert_path, cert_password, sandbox=True, timeout=30, transport=None):
                self.sandbox = sandbox
    """,
    "verifactu_py/clients/auth.py": """
        async def create_async_certificate_client(cert_path, cert_password, transport=None):
            return None
    """,
    "verifactu_py/services/blockchain.py": """
        class BlockchainChain:
            def __init__(self, issuer_nif, storage_path=None, clock=None, store=None):
                self.issuer_nif = issuer_nif
            async def register(self, invoice):
                return invoice
            def last_hash(self):
                return ""
    """,
    "verifactu_py/services/storage.py": """
        def get_default_storage_path():
            return "."
    """,
    "verifactu_py/services/xml_generator.py": """
        def generate_registro_alta_xml(
            invoice,
            validate=True,
            system_info=None,
            generation_timestamp=None,
            previous_record=None,
        ):
            return ""
        def generate_registro_anulacion_xml(
            issuer, number, *, previous_hash=None, validate=True, generation_timestamp=None
        ):
            return ""
    """,
}
_PV_BREAKS: set[Break] = {
    ("verifactu_py.services.BlockchainChain.add_invoice", "removed", ""),
    ("verifactu_py.services.BlockchainChain.verify_chain", "removed", ""),
    ("verifactu_py.services.storage.save_chain", "removed", ""),
    ("verifactu_py.services.storage.load_chain", "removed", ""),
    # The command loses `--blockchain`; the import of `Invoice` it dropped was no re-export.
    ("verifactu_py.cli.register", "param_removed", "blockchain"),
}


def _row(name: str, base: str, head: str, breaks: set[Break]) -> tuple[str, Tree, Tree, set[Break]]:
    """A one-module case: `pkg/__init__.py` before and after."""
    return name, {"pkg/__init__.py": base}, {"pkg/__init__.py": head}, breaks


_CASES: list[tuple[str, Tree, Tree, set[Break]]] = [
    ("Fable: los diez cambios del repositorio sintético", _FABLE_BASE, _FABLE_HEAD, _FABLE_BREAKS),
    ("pyverifactu: la fase 4a", _PV_BASE, _PV_HEAD, _PV_BREAKS),
    _row("nada cambia", "def f(a):\n    pass", "def f(a):\n    pass", set()),
    _row("cambia el cuerpo", "def f(a):\n    return 1", "def f(a):\n    return 2", set()),
    _row("un parámetro nuevo con valor", "def f(a):\n    pass", "def f(a, b=1):\n    pass", set()),
    _row(
        "un parámetro nuevo sin valor",
        "def f(a):\n    pass",
        "def f(a, b):\n    pass",
        {("pkg.f", "param_new", "b")},
    ),
    _row(
        "uno solo por nombre nuevo sin valor",
        "def f(a):\n    pass",
        "def f(a, *, b):\n    pass",
        {("pkg.f", "param_new", "b")},
    ),
    _row(
        "un parámetro que cambia de nombre",
        "def f(a):\n    pass",
        "def f(b):\n    pass",
        {("pkg.f", "param_removed", "a"), ("pkg.f", "param_new", "b")},
    ),
    _row(
        "dos parámetros que cambian de sitio",
        "def f(a, b):\n    pass",
        "def f(b, a):\n    pass",
        {("pkg.f", "param_moved", "a"), ("pkg.f", "param_moved", "b")},
    ),
    _row(
        "uno nuevo con valor delante de otro",
        "def f(a):\n    pass",
        "def f(z=0, a=1):\n    pass",
        {("pkg.f", "param_moved", "a")},
    ),
    _row(
        "un parámetro que pierde su valor",
        "def f(a=1):\n    pass",
        "def f(a):\n    pass",
        {("pkg.f", "param_default", "a")},
    ),
    _row("uno que gana su valor", "def f(a):\n    pass", "def f(a=1):\n    pass", set()),
    _row(
        "solo por nombre pasa a posicional o por nombre",
        "def f(*, a):\n    pass",
        "def f(a):\n    pass",
        set(),
    ),
    _row(
        "solo posicional pasa a posicional o por nombre",
        "def f(a, /):\n    pass",
        "def f(a):\n    pass",
        set(),
    ),
    _row(
        "posicional o por nombre pasa a solo posicional",
        "def f(a):\n    pass",
        "def f(a, /):\n    pass",
        {("pkg.f", "param_kind", "a")},
    ),
    _row(
        "solo posicional que cambia de nombre",
        "def f(a, /):\n    pass",
        "def f(b, /):\n    pass",
        set(),
    ),
    _row(
        "pierde **kwargs",
        "def f(a, **kw):\n    pass",
        "def f(a):\n    pass",
        {("pkg.f", "param_removed", "kw")},
    ),
    _row("*args cambia de nombre", "def f(*a):\n    pass", "def f(*b):\n    pass", set()),
    _row(
        "deja de ser async",
        "async def f():\n    pass",
        "def f():\n    pass",
        {("pkg.f", "async", "")},
    ),
    _row(
        "una función pasa a clase",
        "def f(a):\n    pass",
        "class f:\n    def __init__(self, a):\n        pass",
        {("pkg.f", "kind", "")},
    ),
    _row(
        "un método pasa a estático",
        "class C:\n    def m(self, a):\n        pass",
        "class C:\n    @staticmethod\n    def m(a):\n        pass",
        {("pkg.C.m", "kind", "")},
    ),
    _row(
        "un método de clase sin cls",
        "class C:\n    @classmethod\n    def m(cls, a):\n        pass",
        "class C:\n    @classmethod\n    def m(cls, a, b=1):\n        pass",
        set(),
    ),
    _row(
        "una propiedad con setter sigue siendo una",
        "class C:\n    @property\n    def p(self):\n        return 1",
        (
            "class C:\n    @property\n    def p(self):\n        return 1\n"
            "    @p.setter\n    def p(self, v):\n        pass"
        ),
        set(),
    ),
    _row(
        "el constructor gana un parámetro sin valor",
        "class C:\n    def __init__(self, a):\n        pass",
        "class C:\n    def __init__(self, a, b):\n        pass",
        {("pkg.C", "param_new", "b")},
    ),
    _row(
        "un constructor heredado del proyecto",
        "class B:\n    def __init__(self, a):\n        pass\nclass C(B):\n    pass",
        "class B:\n    def __init__(self, a, b):\n        pass\nclass C(B):\n    pass",
        {("pkg.B", "param_new", "b"), ("pkg.C", "param_new", "b")},
    ),
    _row(
        "un método que sube a la base",
        "class B:\n    pass\nclass C(B):\n    def m(self):\n        pass",
        "class B:\n    def m(self):\n        pass\nclass C(B):\n    pass",
        set(),
    ),
    _row(
        "un método que se quita de la base",
        "class B:\n    def m(self):\n        pass\nclass C(B):\n    pass",
        "class B:\n    pass\nclass C(B):\n    pass",
        {("pkg.B.m", "removed", ""), ("pkg.C.m", "removed", "")},
    ),
    _row(
        "un atributo de clase",
        "class C:\n    LIMIT = 1",
        "class C:\n    pass",
        {("pkg.C.LIMIT", "removed", "")},
    ),
    _row(
        "un atributo anotado pasa a ClassVar",
        "class C:\n    x: int = 1",
        "from typing import ClassVar\nclass C:\n    x: ClassVar[int] = 1",
        {("pkg.C.x", "kind", "")},
    ),
    _row(
        "un campo nuevo de dataclass sin valor",
        "from dataclasses import dataclass\n@dataclass\nclass D:\n    a: int",
        "from dataclasses import dataclass\n@dataclass\nclass D:\n    a: int\n    b: int",
        {("pkg.D", "param_new", "b")},
    ),
    _row(
        "un campo nuevo de dataclass con field(default_factory)",
        "from dataclasses import dataclass\n@dataclass\nclass D:\n    a: int",
        (
            "from dataclasses import dataclass, field\n@dataclass\nclass D:\n    a: int\n"
            "    b: list = field(default_factory=list)"
        ),
        set(),
    ),
    _row(
        "un campo fuera del __init__ de la dataclass",
        "from dataclasses import dataclass\n@dataclass\nclass D:\n    a: int",
        (
            "from dataclasses import dataclass, field\n@dataclass\nclass D:\n    a: int\n"
            "    b: int = field(init=False)"
        ),
        set(),
    ),
    _row(
        "una dataclass que pasa a kw_only",
        "from dataclasses import dataclass\n@dataclass\nclass D:\n    a: int",
        "from dataclasses import dataclass\n@dataclass(kw_only=True)\nclass D:\n    a: int",
        {("pkg.D", "param_kind", "a")},
    ),
    _row(
        "los campos tras KW_ONLY",
        "from dataclasses import dataclass\n@dataclass\nclass D:\n    a: int\n    b: int = 0",
        (
            "from dataclasses import KW_ONLY, dataclass\n@dataclass\nclass D:\n    a: int\n"
            "    _: KW_ONLY\n    b: int = 0"
        ),
        {("pkg.D", "param_kind", "b")},
    ),
    _row(
        "un campo nuevo de una dataclass hija va tras los de la base",
        (
            "from dataclasses import dataclass\n@dataclass\nclass B:\n    a: int\n"
            "@dataclass\nclass D(B):\n    b: int = 0"
        ),
        (
            "from dataclasses import dataclass\n@dataclass\nclass B:\n    a: int\n"
            "    z: int = 0\n@dataclass\nclass D(B):\n    b: int = 0"
        ),
        {("pkg.D", "param_moved", "b")},
    ),
    _row(
        "un campo obligatorio de un modelo de pydantic",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int",
        (
            "from pydantic import BaseModel, Field\nclass M(BaseModel):\n    a: int\n"
            "    b: int = Field(...)"
        ),
        {("pkg.M", "param_new", "b")},
    ),
    _row(
        "un campo opcional de un modelo de pydantic",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int",
        (
            "from pydantic import BaseModel, Field\nclass M(BaseModel):\n    a: int\n"
            "    b: int = Field(1)"
        ),
        set(),
    ),
    _row(
        "un campo de un NamedTuple",
        "from typing import NamedTuple\nclass T(NamedTuple):\n    a: int\n    b: int = 0",
        "from typing import NamedTuple\nclass T(NamedTuple):\n    a: int",
        {("pkg.T", "param_removed", "b"), ("pkg.T.b", "removed", "")},
    ),
    _row(
        "una clase con una base de fuera no tiene firma que comparar",
        "class E(Exception):\n    pass",
        "class E(Exception):\n    def __init__(self, a, b):\n        pass",
        set(),
    ),
    _row(
        "un miembro de Enum que pasa a valor de otra clase no cambia de nombre",
        "import enum\nclass C(enum.IntEnum):\n    A = 1",
        "import enum\nclass C(enum.IntEnum):\n    A = 2",
        set(),
    ),
    _row(
        "una clase anidada",
        "class C:\n    class Inner:\n        def m(self):\n            pass",
        "class C:\n    class Inner:\n        pass",
        {("pkg.C.Inner.m", "removed", "")},
    ),
    _row("lo que empieza por _ no es API", "def _f(a):\n    pass", "def _f(b):\n    pass", set()),
    _row(
        "un dunder que __all__ nombra sí lo es",
        "__all__ = ['__version__']\n__version__ = '1'",
        "__all__ = []\n__version__ = '1'",
        {("pkg.__version__", "removed", "")},
    ),
    _row(
        "lo que solo importa TYPE_CHECKING no está al ejecutar",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg.sub import x\nY = 1",
        "from typing import TYPE_CHECKING\nY = 1",
        set(),
    ),
    _row(
        "lo que define un try, en cualquiera de sus ramas",
        "try:\n    import json\nexcept ImportError:\n    json = None\nVALUE = 1",
        "VALUE = 1",
        {("pkg.json", "removed", "")},
    ),
    _row(
        "__all__ ampliado con += y extend",
        "__all__ = ['a']\n__all__ += ['b']\n__all__.extend(['c'])\na = b = c = 1",
        "__all__ = ['a']\na = b = c = 1",
        {("pkg.b", "removed", ""), ("pkg.c", "removed", "")},
    ),
]


def _slug(name: str) -> str:
    """A test id of plain words: mutmut can't name back a test whose id has `(` or `:`."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "-".join(re.findall(r"[a-z0-9]+", plain.lower()))


def _write(root: Path, tree: Tree) -> None:
    for name, source in tree.items():
        path = root / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")


def _report(root: Path, tree: Tree, monkeypatch: pytest.MonkeyPatch) -> ApiReport:
    _write(root, tree)
    monkeypatch.chdir(root)
    report = api_report(json.loads(json.dumps({"symbols": lictor_api.measure(["src"])})))
    assert isinstance(report, ApiReport)
    return report


@pytest.mark.parametrize(
    ("base", "head", "breaks"), [c[1:] for c in _CASES], ids=[_slug(c[0]) for c in _CASES]
)
def test_what_breaks_between_two_trees(
    base: Tree, head: Tree, breaks: set[Break], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _report(tmp_path / "base", base, monkeypatch)
    after = _report(tmp_path / "head", head, monkeypatch)

    found = {(b.name, b.change, b.param) for b in all_breaks(before, after)}

    assert found == breaks


def test_a_break_reached_two_ways_is_told_once_by_its_shortest_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _report(tmp_path / "base", _FABLE_BASE, monkeypatch)
    after = _report(tmp_path / "head", _FABLE_HEAD, monkeypatch)

    status = next(b for b in all_breaks(before, after) if b.name == "pkg.Invoice.status")

    assert status.aliases == ("pkg.models.Invoice.status",)
    assert status.detail == "pasa de método a propiedad"


def test_the_seven_parameters_added_with_a_default_break_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _report(tmp_path / "base", _PV_BASE, monkeypatch)
    after = _report(tmp_path / "head", _PV_HEAD, monkeypatch)
    names = {b.name for b in all_breaks(before, after)}

    for added in (
        "verifactu_py.AEATSoapClient",  # not exported by the root: stays out
        "verifactu_py.clients.AEATSoapClient",
        "verifactu_py.clients.auth.create_async_certificate_client",
        "verifactu_py.services.BlockchainChain",
        "verifactu_py.generate_registro_alta_xml",
        "verifactu_py.services.xml_generator.generate_registro_anulacion_xml",
        "verifactu_py.cli.chain_status",
    ):
        assert added not in names


def test_what_each_name_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(tmp_path, _FABLE_HEAD, monkeypatch)
    by_name = {s.name: s for s in report.symbols}

    assert by_name["pkg"].kind == "module"
    assert by_name["pkg.top"].origin == "pkg.core.top"  # re-exported: followed to where it is
    assert by_name["pkg.models.Color.RED"].kind == "member"
    assert by_name["pkg.Invoice.number"].kind == "field"
    assert by_name["pkg.Invoice.load"].is_async
    assert by_name["pkg.models.Child.run"].kind == "method"  # inherited from the project
    assert "pkg._impl" not in by_name
    assert "pkg.dataclass" not in by_name  # an import of a module is no export of it
    pay = by_name["pkg.Invoice.pay"].params
    assert pay is not None
    assert [(p.name, p.kind, p.default) for p in pay] == [
        ("amount", "positional_or_keyword", False),
        ("when", "keyword", True),
    ]


def test_a_package_given_as_its_root_is_named_from_its_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, {"pkg/__init__.py": "def f():\n    pass"})
    monkeypatch.chdir(tmp_path)

    names = {s["name"] for s in lictor_api.measure(["src/pkg"])}

    assert names == {"pkg", "pkg.f"}


def test_a_relative_import_and_a_star_are_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = {
        "pkg/__init__.py": "from .a import *\nfrom . import b as bee\nfrom .b import g as h",
        "pkg/a.py": "__all__ = ['f']\ndef f(x):\n    pass\ndef other():\n    pass",
        "pkg/b.py": "def g(y):\n    pass",
    }
    report = _report(tmp_path, tree, monkeypatch)
    by_name = {s.name: s for s in report.symbols}

    assert by_name["pkg.f"].origin == "pkg.a.f"
    assert by_name["pkg.h"].origin == "pkg.b.g"
    assert by_name["pkg.bee"].kind == "module"
    assert "pkg.other" not in by_name


def test_an_external_name_in_all_is_counted_without_a_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report(
        tmp_path,
        {"pkg/__init__.py": "from json import dumps\n__all__ = ['dumps', 'missing']"},
        monkeypatch,
    )
    by_name = {s.name: s for s in report.symbols}

    assert by_name["pkg.dumps"].kind == "external"
    assert by_name["pkg.dumps"].origin == "json.dumps"
    assert by_name["pkg.missing"].kind == "unresolved"


def test_a_class_that_inherits_from_itself_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = {"pkg/__init__.py": "class A(B):\n    pass\nclass B(A):\n    def m(self):\n        pass"}

    report = _report(tmp_path, tree, monkeypatch)

    assert {"pkg.A", "pkg.B", "pkg.A.m", "pkg.B.m"} <= {s.name for s in report.symbols}


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", lictor_api.__file__, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_writes_its_report_and_counts_the_names(tmp_path: Path) -> None:
    _write(tmp_path, {"pkg/__init__.py": "def f():\n    pass"})

    done = _run("src", "--out", "api.json", cwd=tmp_path)

    assert done.returncode == 0
    assert "2 nombres públicos" in done.stdout
    report = api_report(json.loads((tmp_path / "api.json").read_text(encoding="utf-8")))
    assert isinstance(report, ApiReport)
    assert [s.name for s in report.symbols] == ["pkg", "pkg.f"]


def test_a_file_that_doesnt_parse_fails_and_leaves_no_report(tmp_path: Path) -> None:
    _write(tmp_path, {"pkg/__init__.py": "def f(:\n"})

    done = _run("src", "--out", "api.json", cwd=tmp_path)

    assert done.returncode == 2
    assert not (tmp_path / "api.json").exists()


@pytest.mark.parametrize("args", [(), ("src", "--out")])
def test_a_command_without_what_it_needs_says_how(args: tuple[str, ...], tmp_path: Path) -> None:
    done = _run(*args, cwd=tmp_path)

    assert done.returncode == 2
    assert done.stderr
