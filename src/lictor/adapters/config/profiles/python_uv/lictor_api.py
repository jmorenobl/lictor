"""The public API of the code under test, as the names a user of its packages can reach (ADR-0017).

Lictor copies this file into the verification environment, where it runs as the `api` control. It
imports nothing of Lictor, reads the code without importing or running it and reads no
configuration: what it counts is fixed here, and its tests fix each construct. It only gives
facts: which names are public and what each one is. Comparing two of its reports, and deciding
what breaks, is Lictor's.

A name is public when a user can reach it by importing a public module, as PEP 8 says:

- a module is public when no part of its dotted name starts with `_`;
- with a static `__all__`, a module exports exactly what it lists;
- without one, it exports what it defines (functions, classes, assignments) whose name doesn't
  start with `_`, what it takes with `from x import *`, and, in a package's `__init__.py`, what it
  imports from its own subpackages: that is how a package exposes what its modules define. Any
  other import is an implementation detail;
- each export is followed through its imports to its definition, private modules included, so a
  definition moved and re-exported keeps its name. What `if TYPE_CHECKING:` imports isn't there
  when the code runs, and doesn't count.

Of a class it counts the methods, properties, static and class methods, class attributes, fields
(annotated names: a dataclass's, a pydantic model's) and members of an `Enum`, its own and those
it inherits from classes of the project, with its constructor as its signature: its `__init__`,
the one a dataclass, a `NamedTuple` or a pydantic model builds from its fields, or the nearest one
it inherits. Of each callable, every parameter with its kind (positional only, positional or by
name, by name only, `*args`, `**kwargs`) and whether it has a default, and whether it is `async`.
Names that start with `_` are never public, dunders included, unless `__all__` lists them.

    python -I -B -m lictor_api src [--out report.json]

It prints how many public names it found; with `--out` it also writes them as JSON, which is the
report the control reads. A file that can't be parsed fails the command and leaves no report.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

DEFINITION = (
    "API pública: los nombres que alcanza quien importa un módulo público (sin `_` en su "
    "ruta): su `__all__`, o lo que define sin `_` y, en un `__init__.py`, lo que importa de sus "
    "submódulos, seguido hasta su definición. De cada clase, sus métodos, propiedades, "
    "atributos, campos y miembros, también los heredados del proyecto; de cada invocable, sus "
    "parámetros con su clase y si tienen valor por defecto, y si es async."
)

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"})
_PROPERTIES = frozenset({"property", "cached_property"})
_ACCESSORS = frozenset({"setter", "getter", "deleter"})
_OBJECT = frozenset({"object", "builtins.object"})
# Absent in the oldest interpreters a project may run: then nothing in its code can be one.
_TYPE_ALIAS = getattr(ast, "TypeAlias", None)


@dataclass(frozen=True)
class _Binding:
    """What a name of a module is bound to, as the module's own statements say.

    `def` and `value` carry the statement; `import` a module and the name taken from it
    (`from source import attribute`); `module` a module bound whole (`import x`).
    """

    kind: str
    node: ast.AST | None = None
    source: str = ""
    attribute: str = ""


@dataclass
class _Module:
    """One module of the tree: its name, its file and what each of its names is bound to."""

    name: str
    file: str
    package: bool
    bindings: dict[str, _Binding] = field(default_factory=dict)
    all_names: list[str] | None = None
    stars: list[str] = field(default_factory=list)

    @property
    def public(self) -> bool:
        return not any(part.startswith("_") for part in self.name.split("."))


@dataclass(frozen=True)
class _Target:
    """What a name finally is: a definition in a module, a module, or something outside.

    `kind` is `def`, `value`, `module` or `external`; `where` names the module, or what is
    outside.
    """

    kind: str
    module: _Module | None = None
    node: ast.AST | None = None
    where: str = ""

    def project_class(self) -> tuple[_Module, ast.ClassDef] | None:
        if self.kind == "def" and isinstance(self.node, ast.ClassDef) and self.module:
            return self.module, self.node
        return None


def _strings(node: ast.expr | None) -> list[str] | None:
    """The strings of a literal list or tuple, or None if it isn't one."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    found: list[str] = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        found.append(element.value)
    return found


def _type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _runtime_statements(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]:
    """The statements that bind names when the code runs, in order, inside `if` and `try` too.

    What only exists for a type checker (`if TYPE_CHECKING:`) isn't there at run time.
    """
    for node in body:
        if isinstance(node, ast.If):
            if not _type_checking(node.test):
                yield from _runtime_statements(node.body)
            yield from _runtime_statements(node.orelse)
        elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            yield from _runtime_statements(getattr(node, "body", []))
            for handler in getattr(node, "handlers", []):
                yield from _runtime_statements(handler.body)
            yield from _runtime_statements(getattr(node, "orelse", []))
            yield from _runtime_statements(getattr(node, "finalbody", []))
        else:
            yield node


def _absolute(module: _Module, name: str | None, level: int) -> str | None:
    """The absolute name of what `from <level dots><name> import` names, from `module`."""
    if level == 0:
        return name
    parts = module.name.split(".") if module.package else module.name.split(".")[:-1]
    if level - 1 > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)]
    if name:
        base = [*base, *name.split(".")]
    return ".".join(base) if base else None


def _bind_module(module: _Module, body: Sequence[ast.stmt]) -> None:
    """Fill in what each name of a module is bound to, the last binding winning, as Python does."""
    for node in _runtime_statements(body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module.bindings[node.name] = _Binding("def", node)
        elif isinstance(node, ast.ImportFrom):
            _bind_from(module, node)
        elif isinstance(node, ast.Import):
            _bind_import(module, node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            _bind_value(module, node)
        elif isinstance(node, ast.Expr):
            _extend_all(module, node.value)
        else:
            _bind_alias(module, node)


def _bind_import(module: _Module, node: ast.Import) -> None:
    """`import a.b` binds `a`; `import a.b as c` binds `c` to `a.b`."""
    for alias in node.names:
        bound = alias.asname or alias.name.split(".")[0]
        source = alias.name if alias.asname else bound
        module.bindings[bound] = _Binding("module", source=source)


def _bind_alias(module: _Module, node: ast.stmt) -> None:
    """`type X = ...`, which only Python 3.12 parses: a value like any assignment."""
    name = getattr(node, "name", None) if _TYPE_ALIAS and isinstance(node, _TYPE_ALIAS) else None
    if isinstance(name, ast.Name):
        module.bindings[name.id] = _Binding("value", node)


def _bind_from(module: _Module, node: ast.ImportFrom) -> None:
    source = _absolute(module, node.module, node.level)
    if source is None:
        return
    for alias in node.names:
        if alias.name == "*":
            module.stars.append(source)
        else:
            bound = alias.asname or alias.name
            module.bindings[bound] = _Binding("import", source=source, attribute=alias.name)


def _bind_value(module: _Module, node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if not isinstance(target, ast.Name):
            continue
        if target.id != "__all__":
            if not isinstance(node, ast.AugAssign):
                module.bindings[target.id] = _Binding("value", node)
            continue
        names = _strings(node.value)
        if not isinstance(node, ast.AugAssign):
            module.all_names = names
        elif names is not None and module.all_names is not None:
            module.all_names.extend(names)


def _all_call(value: ast.expr) -> tuple[str, ast.expr] | None:
    """The method and the argument of a call `__all__.<method>(<argument>)`, if it is one."""
    if not isinstance(value, ast.Call) or not value.args:
        return None
    func = value.func
    if not isinstance(func, ast.Attribute) or _dotted(func.value) != "__all__":
        return None
    return func.attr, value.args[0]


def _extend_all(module: _Module, value: ast.expr) -> None:
    """`__all__.extend([...])` and `__all__.append("x")`, the other static forms."""
    call = _all_call(value)
    if module.all_names is None or call is None:
        return
    method, arg = call
    if method == "extend":
        module.all_names.extend(_strings(arg) or [])
    elif method == "append":
        module.all_names.extend(_strings(ast.List(elts=[arg])) or [])


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, (ast.Subscript, ast.Call)):
        return _dotted(node.value if isinstance(node, ast.Subscript) else node.func)
    return "?"


def _last(name: str) -> str:
    return name.rsplit(".", 1)[-1]


class Api:
    """The modules of a tree and the public names they give."""

    def __init__(self, modules: dict[str, _Module]) -> None:
        self.modules = modules
        self.symbols: dict[str, dict[str, object]] = {}

    # Resolution: a name of a module, followed to what it is.

    def resolve(
        self, module_name: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> _Target | None:
        key = (module_name, name)
        module = self.modules.get(module_name)
        if key in seen:
            return None
        if module is None:
            return _Target("external", where=f"{module_name}.{name}")
        seen = seen | {key}
        binding = module.bindings.get(name)
        if binding is not None:
            return self._follow(module, binding, seen)
        for star in module.stars:
            if star in self.modules and name in self.exports(self.modules[star]):
                return self.resolve(star, name, seen)
        submodule = f"{module_name}.{name}"
        if submodule in self.modules:
            return _Target("module", self.modules[submodule], where=submodule)
        return None

    def _follow(
        self, module: _Module, binding: _Binding, seen: frozenset[tuple[str, str]]
    ) -> _Target | None:
        if binding.kind in ("def", "value"):
            return _Target(binding.kind, module, binding.node, module.name)
        if binding.kind == "module":
            if binding.source in self.modules:
                return _Target("module", self.modules[binding.source], where=binding.source)
            return _Target("external", where=binding.source)
        if binding.source not in self.modules:
            return _Target("external", where=f"{binding.source}.{binding.attribute}")
        return self.resolve(binding.source, binding.attribute, seen)

    def resolve_expr(self, module: _Module, node: ast.expr) -> _Target | None:
        """What a name or a dotted name written in a module is, as the base of a class."""
        if isinstance(node, ast.Name):
            return self.resolve(module.name, node.id)
        if not isinstance(node, ast.Attribute):
            return None
        owner = self.resolve_expr(module, node.value)
        if owner is None:
            return None
        if owner.kind == "module" and owner.module is not None:
            return self.resolve(owner.module.name, node.attr)
        if owner.kind == "external":
            return _Target("external", where=f"{owner.where}.{node.attr}")
        return None

    def exports(self, module: _Module, seen: frozenset[str] = frozenset()) -> list[str]:
        """The names a module gives to who imports it."""
        if module.all_names is not None:
            return list(dict.fromkeys(module.all_names))
        if module.name in seen:
            return []
        seen = seen | {module.name}
        names = [
            name
            for name, binding in module.bindings.items()
            if not name.startswith("_") and _exported(module, binding)
        ]
        for star in module.stars:
            if star in self.modules:
                found = self.exports(self.modules[star], seen)
                names.extend(n for n in found if not n.startswith("_"))
        return list(dict.fromkeys(names))

    # The public names.

    def collect(self) -> list[dict[str, object]]:
        for module in sorted(self.modules.values(), key=lambda m: m.name):
            if not module.public:
                continue
            self._add(module.name, _symbol("module", module.name, module.file, 1))
            for name in self.exports(module):
                self._export(module, name)
        return [self.symbols[name] for name in sorted(self.symbols)]

    def _add(self, name: str, symbol: dict[str, object]) -> bool:
        if name in self.symbols:
            return False
        self.symbols[name] = {"name": name, **symbol}
        return True

    def _export(self, module: _Module, name: str) -> None:
        public = f"{module.name}.{name}"
        target = self.resolve(module.name, name)
        if target is None:
            self._add(public, _symbol("unresolved", public, module.file, 1))
        elif target.kind == "external":
            self._add(public, _symbol("external", target.where, module.file, 1))
        elif target.kind == "module" and target.module is not None:
            self._add(public, _symbol("module", target.module.name, target.module.file, 1))
        elif target.module is not None and target.node is not None:
            self._definition(public, target.module, target.node)

    def _definition(self, public: str, module: _Module, node: ast.AST) -> None:
        line = getattr(node, "lineno", 1)
        if isinstance(node, _FUNCTIONS):
            symbol = _symbol("function", f"{module.name}.{node.name}", module.file, line)
            self._add(public, _callable(symbol, node, _params(node.args)))
        elif isinstance(node, ast.ClassDef):
            self._class(public, _Place(module, f"{module.name}.{node.name}"), node)
        else:
            origin = f"{module.name}.{_last(public)}"
            self._add(public, _symbol("constant", origin, module.file, line))

    # Classes.

    def _class(self, public: str, place: _Place, node: ast.ClassDef) -> None:
        symbol = _symbol("class", place.origin, place.module.file, node.lineno)
        symbol["params"] = self.constructor(place.module, node, frozenset())
        if not self._add(public, symbol) or id(node) in place.outer:
            return
        inner = (*place.outer, id(node))
        enum = self.is_enum(place.module, node, frozenset())
        for name, (owner, member) in self.members(place.module, node, frozenset()).items():
            here = _Place(owner, f"{place.origin}.{name}", inner)
            self._member(f"{public}.{name}", here, member, enum)

    def _member(self, public: str, place: _Place, member: ast.AST, enum: bool) -> None:
        line = getattr(member, "lineno", 1)
        if isinstance(member, ast.ClassDef):
            self._class(public, place, member)
        elif isinstance(member, _FUNCTIONS):
            kind = _method_kind(member)
            symbol = _symbol(kind, place.origin, place.module.file, line)
            params = None if kind == "property" else _params(member.args)
            if params is not None and kind != "staticmethod":
                params = params[1:]
            self._add(public, _callable(symbol, member, params))
        else:
            kind = _attribute_kind(member, enum)
            self._add(public, _symbol(kind, place.origin, place.module.file, line))

    def bases(self, module: _Module, node: ast.ClassDef) -> list[_Target]:
        found: list[_Target] = []
        for base in node.bases:
            target = self.resolve_expr(module, base)
            found.append(target if target is not None else _Target("external", where=_dotted(base)))
        return found

    def project_bases(
        self, module: _Module, node: ast.ClassDef
    ) -> Iterator[tuple[_Module, ast.ClassDef]]:
        for base in self.bases(module, node):
            found = base.project_class()
            if found is not None:
                yield found

    def members(
        self, module: _Module, node: ast.ClassDef, seen: frozenset[int]
    ) -> dict[str, tuple[_Module, ast.AST]]:
        """A class's public members, its own first and then those it inherits from the project."""
        if id(node) in seen:
            return {}
        seen = seen | {id(node)}
        found: dict[str, tuple[_Module, ast.AST]] = {}
        for name, member in _own_members(node):
            found[name] = (module, member)
        for base_module, base in self.project_bases(module, node):
            for name, inherited in self.members(base_module, base, seen).items():
                found.setdefault(name, inherited)
        return found

    def is_enum(self, module: _Module, node: ast.ClassDef, seen: frozenset[int]) -> bool:
        if id(node) in seen:
            return False
        seen = seen | {id(node)}
        for base in self.bases(module, node):
            if base.kind == "external" and _last(base.where) in _ENUM_BASES:
                return True
            found = base.project_class()
            if found is not None and self.is_enum(found[0], found[1], seen):
                return True
        return False

    def family(self, module: _Module, node: ast.ClassDef, seen: frozenset[int]) -> str | None:
        """Who builds the constructor from the fields: a pydantic model, or a `NamedTuple`."""
        if id(node) in seen:
            return None
        seen = seen | {id(node)}
        for base in self.bases(module, node):
            last = _last(base.where)
            if base.kind == "external" and last == "NamedTuple":
                return "namedtuple"
            if base.kind == "external" and last == "BaseModel":
                return "model"
            found = base.project_class()
            family = self.family(found[0], found[1], seen) if found is not None else None
            if family is not None:
                return family
        return None

    def constructor(
        self, module: _Module, node: ast.ClassDef, seen: frozenset[int]
    ) -> list[dict[str, object]] | None:
        """The parameters a class is called with, or None if something outside decides them."""
        if id(node) in seen:
            return None
        seen = seen | {id(node)}
        options = _dataclass(node)
        if options is not None and options.get("init", True) is not False:
            return self._built(module, node, _dataclass_field, options.get("kw_only") is True)
        init = _own_init(node)
        if init is not None:
            return _params(init.args)[1:]
        family = self.family(module, node, frozenset())
        if family is not None:
            fields_of = _model_field if family == "model" else _tuple_field
            return self._built(module, node, fields_of, everything_by_name=False)
        if self.is_enum(module, node, frozenset()):
            return None
        return self._inherited_constructor(module, node, seen)

    def _inherited_constructor(
        self, module: _Module, node: ast.ClassDef, seen: frozenset[int]
    ) -> list[dict[str, object]] | None:
        for base in self.bases(module, node):
            found = base.project_class()
            if found is not None:
                return self.constructor(found[0], found[1], seen)
            if base.where not in _OBJECT:
                return None
        return []

    def fields(
        self, module: _Module, node: ast.ClassDef, field_of: _FieldOf, seen: frozenset[int]
    ) -> list[tuple[str, bool, bool]]:
        """The fields of a class and of its project bases, the bases' first: name, default, by
        name only. A field redefined keeps its first place, with its new facts, as a dataclass
        does."""
        if id(node) in seen:
            return []
        seen = seen | {id(node)}
        found: dict[str, tuple[bool, bool]] = {}
        for base_module, base in reversed(list(self.project_bases(module, node))):
            for name, default, keyword in self.fields(base_module, base, field_of, seen):
                found[name] = (default, keyword)
        for name, default, keyword in _class_fields(node, field_of):
            found[name] = (default, keyword)
        return [(name, default, keyword) for name, (default, keyword) in found.items()]

    def _built(
        self, module: _Module, node: ast.ClassDef, field_of: _FieldOf, everything_by_name: bool
    ) -> list[dict[str, object]]:
        return [
            {
                "name": name,
                "kind": "keyword" if keyword or everything_by_name else "positional_or_keyword",
                "default": default,
            }
            for name, default, keyword in self.fields(module, node, field_of, frozenset())
        ]


@dataclass(frozen=True)
class _Place:
    """Where a definition is being named: the module that holds it, its origin and the classes
    being named around it, so a class nested in itself ends."""

    module: _Module
    origin: str
    outer: tuple[int, ...] = ()


# Whether an annotated name of a class body is a field of its constructor: (default, by name), or
# None if it isn't one.
_FieldOf = Callable[[ast.AnnAssign], "tuple[bool, bool] | None"]


def _exported(module: _Module, binding: _Binding) -> bool:
    """Whether a module without `__all__` exports a name it binds: what it defines, and in a
    package what it imports from its own subpackages."""
    if binding.kind in ("def", "value"):
        return True
    if binding.kind != "import" or not module.package:
        return False
    # `from . import sub` names the package itself, and takes one of its submodules.
    own = binding.source == module.name or binding.source.startswith(f"{module.name}.")
    return own


def _symbol(kind: str, origin: str, file: str, line: int) -> dict[str, object]:
    return {
        "kind": kind,
        "origin": origin,
        "file": file,
        "line": line,
        "async": False,
        "params": None,
    }


def _callable(
    symbol: dict[str, object],
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    params: list[dict[str, object]] | None,
) -> dict[str, object]:
    return {**symbol, "async": isinstance(node, ast.AsyncFunctionDef), "params": params}


def _method_kind(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    for decorator in node.decorator_list:
        last = _last(_dotted(decorator))
        if last in _PROPERTIES:
            return "property"
        if last in ("staticmethod", "classmethod"):
            return last
    return "method"


def _attribute_kind(member: ast.AST, enum: bool) -> str:
    if enum:
        assigned = isinstance(member, ast.Assign) or (
            isinstance(member, ast.AnnAssign) and member.value is not None
        )
        return "member" if assigned else "attribute"
    if isinstance(member, ast.AnnAssign) and not _class_var(member.annotation):
        return "field"
    return "attribute"


def _accessor(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A property's setter, getter or deleter: the property already counts."""
    return any(isinstance(d, ast.Attribute) and d.attr in _ACCESSORS for d in node.decorator_list)


def _own_members(node: ast.ClassDef) -> list[tuple[str, ast.AST]]:
    found: dict[str, ast.AST] = {}
    for statement in _runtime_statements(node.body):
        for name in _member_names(statement):
            if not name.startswith("_"):
                found[name] = statement
    return list(found.items())


def _member_names(statement: ast.stmt) -> list[str]:
    """The names a statement of a class body gives the class."""
    if isinstance(statement, _FUNCTIONS):
        return [] if _accessor(statement) else [statement.name]
    if isinstance(statement, ast.ClassDef):
        return [statement.name]
    if isinstance(statement, ast.Assign):
        return [t.id for t in statement.targets if isinstance(t, ast.Name)]
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return [] if _kw_only_marker(statement.annotation) else [statement.target.id]
    return []


def _own_init(node: ast.ClassDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    init = None
    for statement in _runtime_statements(node.body):
        if isinstance(statement, _FUNCTIONS) and statement.name == "__init__":
            init = statement
    return init


def _class_var(annotation: ast.expr) -> bool:
    return _last(_dotted(annotation)) == "ClassVar"


def _kw_only_marker(annotation: ast.expr) -> bool:
    return _last(_dotted(annotation)) == "KW_ONLY"


def _dataclass(node: ast.ClassDef) -> dict[str, object] | None:
    """The options of a class's `@dataclass`, or None if it isn't one."""
    for decorator in node.decorator_list:
        if _last(_dotted(decorator)) != "dataclass":
            continue
        options: dict[str, object] = {}
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if keyword.arg and isinstance(keyword.value, ast.Constant):
                    options[keyword.arg] = keyword.value.value
        return options
    return None


def _call_named(node: ast.expr | None, name: str) -> ast.Call | None:
    if isinstance(node, ast.Call) and _last(_dotted(node.func)) == name:
        return node
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _is_constant(node: ast.expr | None, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


def _dataclass_field(node: ast.AnnAssign) -> tuple[bool, bool] | None:
    """A dataclass field's default and whether it goes by name; None if `__init__` hasn't it."""
    call = _call_named(node.value, "field")
    if call is None:
        return node.value is not None, False
    if _is_constant(_keyword(call, "init"), False):
        return None
    default = any(_keyword(call, k) is not None for k in ("default", "default_factory"))
    return default, _is_constant(_keyword(call, "kw_only"), True)


def _model_field(node: ast.AnnAssign) -> tuple[bool, bool] | None:
    """A pydantic field goes by name; `= ...` and `Field(...)` alone say it has no default."""
    value = node.value
    if value is None or _is_constant(value, Ellipsis):
        return False, True
    call = _call_named(value, "Field")
    if call is None:
        return True, True
    first = call.args[0] if call.args else _keyword(call, "default")
    if first is not None:
        return not _is_constant(first, Ellipsis), True
    return _keyword(call, "default_factory") is not None, True


def _tuple_field(node: ast.AnnAssign) -> tuple[bool, bool] | None:
    return node.value is not None, False


def _class_fields(node: ast.ClassDef, field_of: _FieldOf) -> list[tuple[str, bool, bool]]:
    """The annotated names of a class body that are fields, in order: name, default, by name."""
    found: list[tuple[str, bool, bool]] = []
    by_name = False
    for statement in _runtime_statements(node.body):
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
            continue
        if _kw_only_marker(statement.annotation):
            by_name = True
            continue
        if _class_var(statement.annotation) or statement.target.id.startswith("_"):
            continue
        facts = field_of(statement)
        if facts is not None:
            found.append((statement.target.id, facts[0], facts[1] or by_name))
    return found


def _params(arguments: ast.arguments) -> list[dict[str, object]]:
    """Every parameter of a callable, in order, with its kind and whether it has a default."""
    positional = [*arguments.posonlyargs, *arguments.args]
    first_default = len(positional) - len(arguments.defaults)
    found: list[dict[str, object]] = []
    for index, arg in enumerate(positional):
        kind = "positional" if index < len(arguments.posonlyargs) else "positional_or_keyword"
        found.append({"name": arg.arg, "kind": kind, "default": index >= first_default})
    if arguments.vararg is not None:
        found.append({"name": arguments.vararg.arg, "kind": "var_positional", "default": False})
    for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        found.append({"name": arg.arg, "kind": "keyword", "default": default is not None})
    if arguments.kwarg is not None:
        found.append({"name": arguments.kwarg.arg, "kind": "var_keyword", "default": False})
    return found


def _module_name(path: Path, start: Path) -> tuple[str, bool]:
    parts = list(path.relative_to(start).with_suffix("").parts)
    package = parts[-1] == "__init__"
    if package:
        parts = parts[:-1]
    return ".".join(parts), package


def load(roots: Sequence[str]) -> dict[str, _Module]:
    """Every module under the roots, by its dotted name; a file that doesn't parse raises.

    A root that is a package itself names its modules from its parent: `src/pkg` gives `pkg`.
    """
    modules: dict[str, _Module] = {}
    for root in roots:
        base = Path(root)
        if base.is_file():
            files, start = [base], base.parent
        else:
            files = sorted(base.rglob("*.py"))
            start = base.parent if (base / "__init__.py").is_file() else base
        for path in files:
            name, package = _module_name(path, start)
            if not name:
                continue
            tree = ast.parse(path.read_bytes(), filename=str(path))
            module = _Module(name, path.as_posix(), package)
            _bind_module(module, tree.body)
            modules[name] = module
    return modules


def measure(roots: Sequence[str]) -> list[dict[str, object]]:
    """The public names under the roots, in order of name."""
    return Api(load(roots)).collect()


def _arguments(argv: Sequence[str]) -> argparse.Namespace:
    """The roots to read and where to write the report; a usage error exits 2."""
    parser = argparse.ArgumentParser(prog="lictor_api", description=DEFINITION)
    parser.add_argument("roots", nargs="+", metavar="ruta")
    parser.add_argument("--out", metavar="informe.json")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = _arguments(argv)
    try:
        found = measure(args.roots)
    except (SyntaxError, ValueError, OSError) as exc:
        sys.stderr.write(f"No se pudo analizar: {exc}\n")
        return 2
    sys.stdout.write(f"{DEFINITION}\n{len(found)} nombres públicos\n")
    if args.out:
        Path(args.out).write_text(json.dumps({"symbols": found}, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
