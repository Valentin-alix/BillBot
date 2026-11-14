import ast
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from google.protobuf.descriptor import Descriptor, FieldDescriptor
from utils.protobuf import is_repeated_field

from DBDofusUnity.consts import BOT_SRC_ROOT, PROTOS_ROOT
from DBDofusUnity.proto_mapper_assembly.helpers.non_obf_names import build_filtered_message_namespace
from DBDofusUnity.proto_mapper_assembly.interfaces.auto_mode_mapping_contract import (
    AutoModeMappingContract,
    AutoModeMappingThresholds,
    AutoModeMessageRequirement,
)
from DBDofusUnity.proto_mapper_assembly.parsers.protobuf_dump_cs import (
    build_dump_cs_messages_from_pb2,
    build_protobuf_descriptor_index,
)


class ProtocolUsageError(ValueError):
    pass


@dataclass(frozen=True)
class _Type:
    kind: str
    name: str = ""
    items: tuple["_Type", ...] = ()


type _Types = frozenset[_Type]
_EMPTY: _Types = frozenset()


def _one(kind: str, name: str = "", items: tuple[_Type, ...] = ()) -> _Types:
    return frozenset({_Type(kind, name, items)})


def _union(values: Iterable[_Types]) -> _Types:
    return frozenset().union(*values)


def _sequence(values: _Types) -> _Types:
    return _one("sequence", items=tuple(values))


def _mapping(keys: _Types, values: _Types) -> _Types:
    return _one("map", items=(_Type("keys", items=tuple(keys)), _Type("values", items=tuple(values))))


def _tuple(values: Iterable[_Types]) -> _Types:
    return _one("tuple", items=tuple(_Type("slot", items=tuple(value)) for value in values))


@dataclass
class _Scope:
    name: str
    module: str
    path: Path
    node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
    owner: str = ""
    values: dict[str, _Types] = field(default_factory=dict)
    parameters: dict[str, _Types] = field(default_factory=dict)
    returns: _Types = _EMPTY
    bases: tuple[str, ...] = ()


_REFLECTION_FUNCTIONS = {
    "src.protocol.protocol_game.get_game_msg",
    "src.protocol.protocol_game.get_game_msg_info",
    "src.protocol.protocol_game.get_msg_transformer",
    "src.protocol.protocol_game.set_field_from_mapping_field",
}
_PROTO_METHODS = {
    "ByteSize", "Clear", "ClearExtension", "ClearField", "CopyFrom", "DiscardUnknownFields",
    "FindInitializationErrors", "FromString", "HasExtension", "HasField", "IsInitialized",
    "ListFields", "MergeFrom", "MergeFromString", "ParseFromString", "SerializePartialToString",
    "SerializeToString", "SetInParent", "UnknownFields", "WhichOneof",
}


class _Analyzer:
    def __init__(self, source_root: Path, descriptors: dict[str, Descriptor], namespaces: dict[str, str]):
        self.descriptors = {descriptor.full_name: descriptor for descriptor in descriptors.values()}
        self.namespaces = namespaces
        self.scopes: dict[str, _Scope] = {}
        self.used: defaultdict[str, set[str]] = defaultdict(set)
        self.errors: set[str] = set()
        self.changed = False
        self.collect = False
        self.proto_symbols: dict[str, str] = {}
        for descriptor in descriptors.values():
            module = descriptor.file.name.removesuffix(".proto").replace("/", ".") + "_pb2"
            chain = descriptor.full_name.removeprefix(descriptor.file.package + ".")
            self.proto_symbols[f"{module}.{chain}"] = descriptor.full_name
            self.proto_symbols[f"DBDofusUnity.datas.protos.non_obf.game.{module}.{chain}"] = descriptor.full_name
        for path in sorted(source_root.rglob("*.py")):
            relative = path.relative_to(source_root).with_suffix("")
            parts = (source_root.name, *relative.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            module = ".".join(parts)
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            self._index(_Scope(module, module, path, tree))
        self.closure_owners = {scope.owner for scope in self.scopes.values()}

    def _index(self, scope: _Scope) -> None:
        self.scopes[scope.name] = scope
        for node in self._bindings(scope.node):
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                name = f"{scope.name}.{node.name}"
                scope.values[node.name] = _one("class" if isinstance(node, ast.ClassDef) else "function", name)
                self._index(_Scope(name, scope.module, scope.path, node, scope.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                prefix = node.module
                if node.level:
                    package = scope.module.split(".")[:-node.level]
                    prefix = ".".join([*package, node.module])
                for alias in node.names:
                    scope.values[alias.asname or alias.name] = _one("symbol", f"{prefix}.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    scope.values[alias.asname or alias.name.split(".")[0]] = _one(
                        "symbol", alias.name if alias.asname else alias.name.split(".")[0]
                    )

    def _bindings(self, node: ast.AST) -> Iterator[ast.AST]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | ast.Import | ast.ImportFrom):
                yield child
            else:
                yield from self._bindings(child)

    def _resolve(self, value: _Type, seen: frozenset[str] = frozenset()) -> _Types:
        if value.kind != "symbol":
            return frozenset({value})
        if value.name in seen:
            return _EMPTY
        if value.name in self.proto_symbols:
            return _one("proto_class", self.proto_symbols[value.name])
        scope = self.scopes.get(value.name)
        if scope:
            if isinstance(scope.node, ast.ClassDef):
                return _one("class", scope.name)
            if isinstance(scope.node, ast.FunctionDef | ast.AsyncFunctionDef):
                return _one("function", scope.name)
        parent, _, attr = value.name.rpartition(".")
        if parent in self.scopes and attr in self.scopes[parent].values:
            return _union([
                self._resolve(item, seen | {value.name}) for item in self.scopes[parent].values[attr]
            ])
        return frozenset({value})

    def _lookup(self, name: str, scope: _Scope, env: dict[str, _Types]) -> _Types:
        if name in env:
            values = env[name]
        else:
            current: _Scope | None = scope
            values = _EMPTY
            while current:
                if name in current.values:
                    values = current.values[name]
                    break
                current = self.scopes.get(current.owner)
            if not values:
                values = _one("symbol", name)
        return _union([self._resolve(value) for value in values])

    def _grow(self, target: dict[str, _Types], name: str, values: _Types) -> None:
        combined = target.get(name, _EMPTY) | values
        if combined != target.get(name, _EMPTY):
            target[name] = combined
            self.changed = True

    def _use(self, name: str, field_name: str | None = None) -> None:
        if self.collect:
            fields = self.used[name]
            if field_name is not None:
                fields.add(field_name)

    def _error(self, scope: _Scope, node: ast.expr | ast.stmt, message: str) -> None:
        if self.collect:
            self.errors.add(f"{scope.path}:{node.lineno}: {message}")

    def _field_types(self, descriptor: FieldDescriptor) -> _Types:
        if descriptor.message_type and descriptor.message_type.GetOptions().map_entry:
            key, value = descriptor.message_type.fields
            return _mapping(self._field_types(key), self._field_types(value))
        values = (
            _one("proto", descriptor.message_type.full_name)
            if descriptor.message_type and descriptor.message_type.full_name in self.descriptors else _EMPTY
        )
        if is_repeated_field(descriptor):
            return _sequence(values)
        return values

    def _class_attr(self, name: str, attr: str, seen: frozenset[str] = frozenset()) -> _Types:
        if name in seen or name not in self.scopes:
            return _EMPTY
        scope = self.scopes[name]
        value = scope.values.get(attr, _EMPTY)
        if value:
            result: set[_Type] = set()
            for item in value:
                if item.kind == "function":
                    method = self.scopes[item.name]
                    if isinstance(method.node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                        isinstance(decorator, ast.Name) and decorator.id in {"property", "cached_property"}
                        for decorator in method.node.decorator_list
                    ):
                        result.update(method.returns)
                    else:
                        result.add(_Type("bound", item.name, (_Type("instance", name),)))
                else:
                    result.update(self._resolve(item))
            return frozenset(result)
        return _union([self._class_attr(base, attr, seen | {name}) for base in scope.bases])

    def _attribute(self, values: _Types, attr: str, scope: _Scope, node: ast.expr | ast.stmt) -> _Types:
        result: set[_Type] = set()
        proto_names: list[str] = []
        matched = False
        for value in values:
            if value.kind in {"proto", "proto_class"}:
                descriptor = self.descriptors[value.name]
                self._use(value.name)
                if value.kind == "proto_class":
                    nested = descriptor.nested_types_by_name.get(attr)
                    if nested and not nested.GetOptions().map_entry:
                        result.add(_Type("proto_class", nested.full_name))
                        matched = True
                    elif attr in descriptor.enum_types_by_name or attr in descriptor.enum_values_by_name:
                        matched = True
                    elif attr in {"DESCRIPTOR", "__name__", "__class__", "__module__"}:
                        matched = True
                    continue
                proto_names.append(value.name)
                if attr in descriptor.fields_by_name:
                    matched = True
                    self._use(value.name, attr)
                    result.update(self._field_types(descriptor.fields_by_name[attr]))
                elif attr in _PROTO_METHODS:
                    matched = True
                    result.add(_Type("proto_method", attr, (value,)))
                elif attr in {"DESCRIPTOR", "__class__"}:
                    matched = True
            elif value.kind == "generic_proto":
                if not any(item.kind == "proto" for item in values):
                    if attr in _PROTO_METHODS:
                        result.add(_Type("proto_method", attr))
                    elif attr not in {"DESCRIPTOR", "__class__"}:
                        self._error(scope, node, f"Unresolved protobuf receiver for field {attr!r}")
            elif value.kind in {"class", "instance"}:
                result.update(self._class_attr(value.name, attr))
            elif value.kind == "symbol":
                result.update(self._resolve(_Type("symbol", f"{value.name}.{attr}")))
            elif value.kind in {"sequence", "map", "tuple"}:
                result.add(_Type("container_method", attr, (value,)))
        if proto_names and not matched:
            self._error(scope, node, f"Unknown protobuf field {attr!r} on {', '.join(sorted(proto_names))}")
        return frozenset(result)

    def _annotation(self, node: ast.expr | None, scope: _Scope, env: dict[str, _Types]) -> _Types:
        if node is None:
            return _EMPTY
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self._annotation(ast.parse(node.value, mode="eval").body, scope, env)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._annotation(node.left, scope, env) | self._annotation(node.right, scope, env)
        if isinstance(node, ast.Subscript):
            parts = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            args = [self._annotation(part, scope, env) for part in parts]
            name = ast.unparse(node.value).rsplit(".", 1)[-1]
            if name in {"list", "set", "frozenset", "Sequence", "Iterable", "Iterator", "Collection", "deque"}:
                return _sequence(_union(args))
            if name in {"dict", "defaultdict", "Mapping", "MutableMapping"} and len(args) == 2:
                return _mapping(args[0], args[1])
            if name == "tuple":
                return _tuple(args)
            if name == "type":
                return frozenset(_Type("proto_class" if item.kind == "proto" else "class", item.name) for item in _union(args))
            if name in {"Optional", "Union", "Annotated"}:
                return _union(args)
            return self._annotation(node.value, scope, env)
        values = self._expr(node, scope, env)
        if any(value.kind == "symbol" and value.name == "google.protobuf.message.Message" for value in values):
            return _one("generic_proto")
        if any(value.kind == "symbol" and value.name.startswith("DBDofusUnity.datas.protos.non_obf.connection.") for value in values):
            return _one("external_proto")
        return frozenset(
            _Type("proto", value.name) if value.kind == "proto_class" else
            _Type("instance", value.name) if value.kind == "class" else value
            for value in values if value.kind != "symbol"
        )

    def _carries_objects(self, value: _Type) -> bool:
        return value.kind in {"proto", "proto_class", "instance", "class", "function", "bound"} or any(
            self._carries_objects(item) for item in value.items
        )

    def _elements(self, values: _Types, index: int | None = None) -> _Types:
        result: set[_Type] = set()
        for value in values:
            if value.kind == "sequence":
                result.update(value.items)
            elif value.kind == "tuple":
                slots = value.items if index is None else value.items[index:index + 1]
                for slot in slots:
                    result.update(slot.items)
            elif value.kind == "map":
                result.update(value.items[1].items)
        return frozenset(result)

    def _assign(self, target: ast.expr, values: _Types, scope: _Scope, env: dict[str, _Types]) -> None:
        if isinstance(target, ast.Name):
            env[target.id] = values
            if isinstance(scope.node, ast.Module | ast.ClassDef) or scope.name in self.closure_owners:
                self._grow(scope.values, target.id, values)
        elif isinstance(target, ast.Tuple | ast.List):
            for index, child in enumerate(target.elts):
                self._assign(child, self._elements(values, index), scope, env)
        elif isinstance(target, ast.Attribute):
            owners = self._expr(target.value, scope, env)
            for owner in owners:
                if owner.kind == "instance" and owner.name in self.scopes:
                    self._grow(self.scopes[owner.name].values, target.attr, values)
            self._attribute(owners, target.attr, scope, target)
        elif isinstance(target, ast.Subscript):
            self._expr(target, scope, env)
            if values:
                containers = self._expr(target.value, scope, env)
                updated: set[_Type] = set()
                for container in containers:
                    if container.kind == "map":
                        keys = frozenset(container.items[0].items) | self._expr(target.slice, scope, env)
                        contents = frozenset(container.items[1].items) | values
                        updated.add(_Type("map", items=(
                            _Type("keys", items=tuple(keys)), _Type("values", items=tuple(contents)),
                        )))
                    elif container.kind == "sequence":
                        updated.add(_Type("sequence", items=tuple(frozenset(container.items) | values)))
                if updated:
                    self._assign(target.value, frozenset(updated), scope, env)

    def _open_parameter(self, annotation: ast.expr | None, scope: _Scope) -> bool:
        if annotation is None:
            return True
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            return self._open_parameter(ast.parse(annotation.value, mode="eval").body, scope)
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            return self._open_parameter(annotation.left, scope) or self._open_parameter(annotation.right, scope)
        if isinstance(annotation, ast.Name | ast.Attribute):
            values = self._expr(annotation, scope, {})
            return any(value.kind == "symbol" and value.name in {
                "google.protobuf.message.Message", "typing.Any", "Any", "object",
            } for value in values)
        return False

    def _call_function(self, value: _Type, args: list[_Types], kwargs: dict[str, _Types]) -> _Types:
        function = self.scopes.get(value.name)
        if not function or not isinstance(function.node, ast.FunctionDef | ast.AsyncFunctionDef):
            return _EMPTY
        if value.kind == "bound":
            args = [frozenset(value.items), *args]
        positional = [*function.node.args.posonlyargs, *function.node.args.args]
        incoming = dict(zip((parameter.arg for parameter in positional), args)) | kwargs
        for parameter in [*positional, *function.node.args.kwonlyargs]:
            if self._open_parameter(parameter.annotation, function):
                self._grow(function.parameters, parameter.arg, incoming.get(parameter.arg, _EMPTY))
        return function.returns

    def _all_fields(self, name: str, seen: frozenset[str] = frozenset()) -> None:
        if name in seen:
            return
        for descriptor in self.descriptors[name].fields:
            self._use(name, descriptor.name)
            child = descriptor.message_type
            if child and child.GetOptions().map_entry:
                child = child.fields_by_name["value"].message_type
            if child and child.full_name in self.descriptors:
                self._all_fields(child.full_name, seen | {name})

    def _proto_call(self, method: _Type, node: ast.Call, scope: _Scope, env: dict[str, _Types]) -> _Types:
        if not method.items:
            if method.name in {"HasField", "ClearField", "WhichOneof", "CopyFrom", "MergeFrom", "ListFields", "Clear"}:
                self._error(scope, node, f"Unresolved protobuf receiver for {method.name}")
            return _EMPTY
        receiver = method.items[0]
        descriptor = self.descriptors[receiver.name]
        if method.name in {"HasField", "ClearField", "WhichOneof"}:
            names = self._expr(node.args[0], scope, env) if node.args else _EMPTY
            strings = {value.name for value in names if value.kind == "string"}
            if not strings or any(value.kind != "string" for value in names):
                self._error(scope, node, f"Unresolved protobuf name in {method.name} on {receiver.name}")
            result: set[_Type] = set()
            for name in strings:
                oneof = descriptor.oneofs_by_name.get(name)
                if oneof:
                    for member in oneof.fields:
                        self._use(receiver.name, member.name)
                        result.add(_Type("string", member.name))
                elif method.name != "WhichOneof" and name in descriptor.fields_by_name:
                    self._use(receiver.name, name)
                else:
                    self._error(scope, node, f"Unknown protobuf field/oneof {name!r} on {receiver.name}")
            return frozenset(result) if method.name == "WhichOneof" else _EMPTY
        if method.name in {"CopyFrom", "MergeFrom", "ListFields", "Clear"}:
            self._all_fields(receiver.name)
        return _EMPTY

    def _call(self, node: ast.Call, scope: _Scope, env: dict[str, _Types]) -> _Types:
        args = [self._expr(arg, scope, env) for arg in node.args]
        kwargs = {keyword.arg: self._expr(keyword.value, scope, env) for keyword in node.keywords if keyword.arg}
        name = ast.unparse(node.func).rsplit(".", 1)[-1]
        if name in {"getattr", "setattr", "hasattr"} and len(args) >= 2:
            protobuf = any(value.kind in {"proto", "generic_proto"} for value in args[0])
            names = {value.name for value in args[1] if value.kind == "string"}
            if protobuf and (not names or any(value.kind != "string" for value in args[1])):
                self._error(scope, node, "Unresolved dynamic protobuf field name")
            return _union([self._attribute(args[0], attr, scope, node) for attr in sorted(names)])
        if name == "cast" and len(node.args) >= 2:
            return self._annotation(node.args[0], scope, env) or args[1]
        if name in {"list", "set", "frozenset", "iter", "sorted", "reversed", "filter"}:
            elements = self._elements(args[-1]) if args else _EMPTY
            return _sequence(elements)
        if name == "next":
            return self._elements(args[0]) if args else _EMPTY
        if name == "enumerate" and args:
            return _sequence(_tuple((_EMPTY, self._elements(args[0]))))
        if name == "zip":
            return _sequence(_tuple(self._elements(arg) for arg in args))
        if name in {"min", "max"}:
            return self._elements(args[0]) if len(args) == 1 else _union(args)
        result: set[_Type] = set()
        for value in self._expr(node.func, scope, env):
            if value.kind == "proto_class":
                self._use(value.name)
                for keyword in node.keywords:
                    if keyword.arg:
                        self._attribute(_one("proto", value.name), keyword.arg, scope, node)
                    else:
                        self._error(scope, node, f"Unresolved protobuf constructor **kwargs for {value.name}")
                result.add(_Type("proto", value.name))
            elif value.kind in {"function", "bound"}:
                result.update(self._call_function(value, args, kwargs))
            elif value.kind == "class":
                result.add(_Type("instance", value.name))
                for keyword, types in kwargs.items():
                    if value.name in self.scopes:
                        self._grow(self.scopes[value.name].values, keyword, types)
                for method in self._class_attr(value.name, "__init__"):
                    self._call_function(method, args, kwargs)
            elif value.kind == "proto_method":
                result.update(self._proto_call(value, node, scope, env))
            elif value.kind == "container_method":
                container = value.items[0]
                if value.name in {"get", "pop", "setdefault"}:
                    result.update(self._elements(frozenset({container})))
                elif value.name == "values" and container.kind == "map":
                    result.add(_Type("sequence", items=container.items[1].items))
                elif value.name == "items" and container.kind == "map":
                    result.add(_Type("sequence", items=(_Type("tuple", items=container.items),)))
                elif value.name in {"add", "append", "extend", "insert"}:
                    elements = self._elements(frozenset({container}))
                    if value.name == "add":
                        for element in elements:
                            if element.kind == "proto":
                                for keyword in kwargs:
                                    self._attribute(frozenset({element}), keyword, scope, node)
                        result.update(elements)
                    elif isinstance(node.func, ast.Attribute) and args:
                        added = self._elements(args[0]) if value.name == "extend" else args[-1]
                        self._assign(
                            node.func.value, _sequence(elements | added), scope, env
                        )
            elif value.kind == "symbol" and value.name in {
                "google.protobuf.json_format.MessageToDict", "google.protobuf.json_format.ParseDict",
            }:
                for arg in args:
                    for item in arg:
                        if item.kind == "proto":
                            self._all_fields(item.name)
        return frozenset(result)

    def _expr(self, node: ast.expr | None, scope: _Scope, env: dict[str, _Types]) -> _Types:
        if node is None:
            return _EMPTY
        if isinstance(node, ast.Await):
            result = self._expr(node.value, scope, env)
        elif isinstance(node, ast.Name):
            result = self._lookup(node.id, scope, env)
        elif isinstance(node, ast.Constant):
            result = _one("string", node.value) if isinstance(node.value, str) else _EMPTY
        elif isinstance(node, ast.Attribute):
            result = self._attribute(self._expr(node.value, scope, env), node.attr, scope, node)
        elif isinstance(node, ast.Call):
            result = self._call(node, scope, env)
        elif isinstance(node, ast.Subscript):
            values = self._expr(node.value, scope, env)
            self._expr(node.slice, scope, env)
            index = node.slice.value if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int) else None
            result = self._elements(values, index)
        elif isinstance(node, ast.List | ast.Set):
            result = _sequence(_union(self._expr(item, scope, env) for item in node.elts))
        elif isinstance(node, ast.Tuple):
            result = _tuple(self._expr(item, scope, env) for item in node.elts)
        elif isinstance(node, ast.Dict):
            keys = _union([self._expr(key, scope, env) for key in node.keys if key])
            values = _union([self._expr(value, scope, env) for value in node.values])
            result = _mapping(keys, values)
        elif isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            local = dict(env)
            for generator in node.generators:
                self._assign(generator.target, self._elements(self._expr(generator.iter, scope, local)), scope, local)
                for condition in generator.ifs:
                    self._expr(condition, scope, local)
            if isinstance(node, ast.DictComp):
                keys = self._expr(node.key, scope, local)
                values = self._expr(node.value, scope, local)
                result = _mapping(keys, values)
            else:
                result = _sequence(self._expr(node.elt, scope, local))
        elif isinstance(node, ast.NamedExpr):
            result = self._expr(node.value, scope, env)
            self._assign(node.target, result, scope, env)
        elif isinstance(node, ast.IfExp):
            self._expr(node.test, scope, env)
            result = self._expr(node.body, scope, env) | self._expr(node.orelse, scope, env)
        elif isinstance(node, ast.BinOp):
            result = self._expr(node.left, scope, env) | self._expr(node.right, scope, env)
        elif isinstance(node, ast.BoolOp):
            local = dict(env)
            results: list[_Types] = []
            for value in node.values:
                results.append(self._expr(value, scope, local))
                if isinstance(node.op, ast.And):
                    self._narrow(value, scope, local)
            result = _union(results)
        elif isinstance(node, ast.Starred):
            result = self._elements(self._expr(node.value, scope, env))
        else:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._expr(child, scope, env)
            result = _EMPTY
        for value in result:
            if value.kind in {"proto", "proto_class"}:
                self._use(value.name)
        return result

    def _narrow(self, node: ast.expr, scope: _Scope, env: dict[str, _Types]) -> None:
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            for value in node.values:
                self._narrow(value, scope, env)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance":
            if len(node.args) == 2 and isinstance(node.args[0], ast.Name):
                narrowed = self._annotation(node.args[1], scope, env)
                if narrowed:
                    env[node.args[0].id] = narrowed

    def _statements(self, nodes: list[ast.stmt], scope: _Scope, env: dict[str, _Types]) -> None:
        for node in nodes:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Import | ast.ImportFrom):
                continue
            if isinstance(node, ast.AnnAssign):
                values = self._annotation(node.annotation, scope, env)
                incoming = self._expr(node.value, scope, env)
                self._assign(node.target, values or incoming, scope, env)
            elif isinstance(node, ast.Assign):
                values = self._expr(node.value, scope, env)
                for target in node.targets:
                    self._assign(target, values, scope, env)
            elif isinstance(node, ast.Return):
                values = self._expr(node.value, scope, env)
                values = frozenset(value for value in values if value.kind == "string" or self._carries_objects(value))
                combined = scope.returns | values
                if combined != scope.returns:
                    scope.returns = combined
                    self.changed = True
            elif isinstance(node, ast.For | ast.AsyncFor):
                self._assign(node.target, self._elements(self._expr(node.iter, scope, env)), scope, env)
                self._statements(node.body, scope, env)
                self._statements(node.orelse, scope, env)
            elif isinstance(node, ast.If | ast.While):
                self._expr(node.test, scope, env)
                first, second = dict(env), dict(env)
                self._narrow(node.test, scope, first)
                self._statements(node.body, scope, first)
                self._statements(node.orelse, scope, second)
                for name in first.keys() | second.keys():
                    env[name] = first.get(name, _EMPTY) | second.get(name, _EMPTY)
            else:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.expr):
                        self._expr(child, scope, env)
                    elif isinstance(child, ast.stmt):
                        self._statements([child], scope, env)
                    elif isinstance(child, ast.ExceptHandler | ast.match_case):
                        self._statements(child.body, scope, env)

    def _pass(self) -> None:
        for scope in self.scopes.values():
            if scope.name in _REFLECTION_FUNCTIONS or scope.name.startswith("src.protocol.protocol_game.get_msg_transformer."):
                continue
            env = dict(scope.parameters)
            if isinstance(scope.node, ast.ClassDef):
                scope.bases = tuple(value.name for base in scope.node.bases for value in self._expr(base, scope, env) if value.kind == "class")
            elif isinstance(scope.node, ast.FunctionDef | ast.AsyncFunctionDef):
                owner = self.scopes.get(scope.owner)
                if owner and isinstance(owner.node, ast.ClassDef) and scope.node.args.args:
                    env[scope.node.args.args[0].arg] = _one("instance", owner.name)
                parameters = [*scope.node.args.posonlyargs, *scope.node.args.args, *scope.node.args.kwonlyargs]
                for parameter in parameters:
                    if parameter.annotation:
                        annotated = self._annotation(parameter.annotation, scope, env)
                        if annotated:
                            if self._open_parameter(parameter.annotation, scope) and env.get(parameter.arg):
                                annotated = env[parameter.arg]
                            env[parameter.arg] = annotated
                if scope.name in self.closure_owners:
                    for name, values in env.items():
                        self._grow(scope.values, name, values)
                declared_return = self._annotation(scope.node.returns, scope, env)
                combined = scope.returns | declared_return
                if combined != scope.returns:
                    scope.returns = combined
                    self.changed = True
            self._statements(scope.node.body, scope, env)

    def analyze(self) -> AutoModeMappingContract:
        for _ in range(max(32, len(self.scopes))):
            self.changed = False
            self._pass()
            if not self.changed:
                break
        else:
            raise ProtocolUsageError("Protocol type inference did not converge")
        self.collect = True
        self._pass()
        if self.errors:
            raise ProtocolUsageError("Cannot generate protocol requirements:\n" + "\n".join(sorted(self.errors)))
        return AutoModeMappingContract(
            version=3,
            thresholds=AutoModeMappingThresholds(message_score=0.5, match_margin=0.05, field_score=0.5),
            messages=[
                AutoModeMessageRequirement(message=namespace, fields=sorted(self.used[name]))
                for name, namespace in sorted(self.namespaces.items(), key=lambda item: item[1])
                if name in self.used
            ],
        )


def generate_auto_mode_mapping_contract(
    *, source_root: Path = BOT_SRC_ROOT, protos_dir: Path = PROTOS_ROOT / "non_obf" / "game",
) -> AutoModeMappingContract:
    descriptors = build_protobuf_descriptor_index(protos_dir)
    messages = build_dump_cs_messages_from_pb2(protos_dir)
    namespaces = {
        descriptors[name].full_name: build_filtered_message_namespace(
            is_obf=False, message=message, messages_by_cls=messages
        )
        for name, message in messages.items()
    }
    return _Analyzer(source_root, descriptors, namespaces).analyze()
