import importlib
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from google.protobuf.descriptor import Descriptor, EnumDescriptor, FieldDescriptor, FileDescriptor, OneofDescriptor
from utils.protobuf import is_repeated_field
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage, DumpCSMessageField
from DBDofusUnity.proto_mapper_assembly.interfaces.field_category import FieldCategoryEnum
from DBDofusUnity.proto_mapper_assembly.parsers._clr_type_utils import normalize_clr_type
from DBDofusUnity.proto_mapper_assembly.parsers.clr_types import categorize_field

_PROTO_SCALAR_TO_CLR: dict[int, str] = {
    FieldDescriptor.TYPE_DOUBLE: "double",
    FieldDescriptor.TYPE_FLOAT: "float",
    FieldDescriptor.TYPE_INT64: "long",
    FieldDescriptor.TYPE_UINT64: "ulong",
    FieldDescriptor.TYPE_INT32: "int",
    FieldDescriptor.TYPE_FIXED64: "ulong",
    FieldDescriptor.TYPE_FIXED32: "uint",
    FieldDescriptor.TYPE_BOOL: "bool",
    FieldDescriptor.TYPE_STRING: "string",
    FieldDescriptor.TYPE_BYTES: "pb::ByteString",
    FieldDescriptor.TYPE_UINT32: "uint",
    FieldDescriptor.TYPE_SFIXED32: "int",
    FieldDescriptor.TYPE_SFIXED64: "long",
    FieldDescriptor.TYPE_SINT32: "int",
    FieldDescriptor.TYPE_SINT64: "long",
}


def build_dump_cs_messages_from_pb2(protos_dir: Path) -> dict[str, DumpCSMessage]:
    descriptors_by_name = build_protobuf_descriptor_index(protos_dir)
    enum_names = _collect_enum_names(descriptors_by_name.values())
    return {
        class_name: _descriptor_to_dump_cs(descriptor, enum_names=enum_names)
        for class_name, descriptor in descriptors_by_name.items()
    }


def build_protobuf_descriptor_index(protos_dir: Path) -> dict[str, Descriptor]:
    index: dict[str, Descriptor] = {}
    for subdir in _iter_pb2_directories(protos_dir):
        subdir_str = str(subdir)
        if subdir_str not in sys.path:
            sys.path.insert(0, subdir_str)
        for pb2_path in sorted(subdir.glob("*_pb2.py")):
            module = importlib.import_module(pb2_path.stem)
            for descriptor in _walk_message_descriptors(module.DESCRIPTOR):
                index[_composed_name(descriptor)] = descriptor
    return index


def _iter_pb2_directories(protos_dir: Path) -> list[Path]:
    if any(protos_dir.glob("*_pb2.py")):
        return [protos_dir]
    return sorted(path for path in protos_dir.iterdir() if path.is_dir())


def _walk_message_descriptors(file_descriptor: FileDescriptor) -> Iterator[Descriptor]:
    stack: list[Descriptor] = list(file_descriptor.message_types_by_name.values())
    while stack:
        descriptor = stack.pop()
        if descriptor.GetOptions().map_entry:
            continue
        yield descriptor
        stack.extend(descriptor.nested_types)


def _collect_enum_names(descriptors: Iterable[Descriptor]) -> frozenset[str]:
    names: set[str] = set()
    for descriptor in descriptors:
        for field in descriptor.fields:
            if field.type == FieldDescriptor.TYPE_ENUM and field.enum_type is not None:
                names.add(field.enum_type.name)
            map_entry = field.message_type
            if map_entry is None or not map_entry.GetOptions().map_entry:
                continue
            for map_field in map_entry.fields:
                if map_field.type == FieldDescriptor.TYPE_ENUM and map_field.enum_type is not None:
                    names.add(map_field.enum_type.name)
    return frozenset(names)


_SYNTHETIC_ONEOF_DECL_ORDER_BASE = 10000


def _descriptor_to_dump_cs(
    descriptor: Descriptor,
    *,
    enum_names: frozenset[str],
) -> DumpCSMessage:
    fields = [
        _field_descriptor_to_dump_cs(field, descriptor, enum_names=enum_names)
        for field in sorted(descriptor.fields, key=lambda descriptor_field: descriptor_field.number)
        if field.containing_oneof is None or _is_synthetic_oneof(field.containing_oneof)
    ]
    for oneof in descriptor.oneofs:
        if _is_synthetic_oneof(oneof):
            continue
        fields.extend(_oneof_group_to_dump_cs(oneof, descriptor, enum_names=enum_names))
    return DumpCSMessage(
        file_descriptor=_file_descriptor_name(descriptor.file.name),
        name=descriptor.name,
        fields=fields,
        properties=[],
        namespace=_package_to_namespace(descriptor.file.package),
        parent_name=_parent_name(descriptor),
    )


def _oneof_group_to_dump_cs(
    oneof: OneofDescriptor,
    parent_descriptor: Descriptor,
    *,
    enum_names: frozenset[str],
) -> list[DumpCSMessageField]:
    """Mirror IL2CPP oneofs as synthetic case fields sharing a backing field and discriminant."""
    camel_group_name = _snake_to_camel_with_underscore(oneof.name).removesuffix("_")
    pascal_group_name = _snake_to_pascal(oneof.name)
    case_clr_type = f"{pascal_group_name}OneofCase"
    fields = [
        DumpCSMessageField(
            clr_type="object",
            normalized_type="object",
            category=FieldCategoryEnum.ONEOF,
            memory_offset=0,
            field_name=f"{camel_group_name}_",
        ),
        DumpCSMessageField(
            clr_type=case_clr_type,
            normalized_type=case_clr_type,
            category=FieldCategoryEnum.ENUM,
            memory_offset=0,
            field_name=f"{camel_group_name}Case_",
        ),
    ]
    for decl_order, field in enumerate(sorted(oneof.fields, key=lambda member: member.number)):
        clr_type = _clr_type_from_field(field, parent_descriptor)
        enum_key_type, enum_value_type = _enum_types(field)
        fields.append(
            DumpCSMessageField(
                clr_type=clr_type,
                normalized_type=normalize_clr_type(clr_type),
                category=categorize_field(clr_type, enum_names),
                memory_offset=0,
                field_name=_snake_to_pascal(field.name),
                property_name=_snake_to_pascal(field.name),
                proto_decl_order=_SYNTHETIC_ONEOF_DECL_ORDER_BASE + decl_order,
                oneof_group_name=oneof.name,
                is_synthetic_oneof_variant=True,
                enum_key_type=enum_key_type,
                enum_value_type=enum_value_type,
            )
        )
    return fields


def _field_descriptor_to_dump_cs(
    field: FieldDescriptor,
    parent_descriptor: Descriptor,
    *,
    enum_names: frozenset[str],
) -> DumpCSMessageField:
    clr_type = _clr_type_from_field(field, parent_descriptor)
    enum_key_type, enum_value_type = _enum_types(field)
    return DumpCSMessageField(
        clr_type=clr_type,
        normalized_type=normalize_clr_type(clr_type),
        category=categorize_field(clr_type, enum_names),
        memory_offset=0,
        field_name=_snake_to_camel_with_underscore(field.name),
        property_name=_snake_to_pascal(field.name),
        proto_decl_order=field.number,
        oneof_group_name=None,
        enum_key_type=enum_key_type,
        enum_value_type=enum_value_type,
    )


def _clr_type_from_field(field: FieldDescriptor, parent_descriptor: Descriptor) -> str:
    map_entry = (
        field.message_type
        if field.message_type is not None and field.message_type.GetOptions().map_entry
        else None
    )
    if map_entry is not None:
        key_field, value_field = map_entry.fields[0], map_entry.fields[1]
        key_clr = _scalar_clr_type(key_field, parent_descriptor)
        value_clr = _scalar_clr_type(value_field, parent_descriptor)
        return f"MapField<{key_clr}, {value_clr}>"
    if is_repeated_field(field):
        return f"RepeatedField<{_scalar_clr_type(field, parent_descriptor)}>"
    return _scalar_clr_type(field, parent_descriptor)


def _scalar_clr_type(field: FieldDescriptor, parent_descriptor: Descriptor) -> str:
    if field.type == FieldDescriptor.TYPE_MESSAGE:
        message_type = field.message_type
        assert message_type is not None, "TYPE_MESSAGE implies a message_type"
        return _csharp_relative_name(message_type, parent_descriptor)
    if field.type == FieldDescriptor.TYPE_ENUM:
        enum_type = field.enum_type
        assert enum_type is not None, "TYPE_ENUM implies an enum_type"
        return _csharp_relative_name(enum_type, parent_descriptor)
    return _PROTO_SCALAR_TO_CLR[field.type]


def _csharp_relative_name(
    target: Descriptor | EnumDescriptor,
    parent_descriptor: Descriptor,
) -> str:
    chain: list[str] = []
    current = target
    while current.containing_type is not None and current.containing_type is not parent_descriptor:
        chain.append(current.name)
        current = current.containing_type
    if current.containing_type is parent_descriptor:
        chain.append(current.name)
        chain.reverse()
        return "Types." + ".Types.".join(chain)
    return target.name


def _enum_types(field: FieldDescriptor) -> tuple[str | None, str | None]:
    map_entry = field.message_type
    if map_entry is None or not map_entry.GetOptions().map_entry:
        # Preserve enum value types so repeated enums do not resolve as repeated messages.
        if field.type == FieldDescriptor.TYPE_ENUM and field.enum_type is not None:
            return None, field.enum_type.name
        return None, None
    key_field, value_field = map_entry.fields[0], map_entry.fields[1]
    enum_key_type = (
        key_field.enum_type.name
        if key_field.type == FieldDescriptor.TYPE_ENUM and key_field.enum_type is not None
        else None
    )
    enum_value_type = (
        value_field.enum_type.name
        if value_field.type == FieldDescriptor.TYPE_ENUM and value_field.enum_type is not None
        else None
    )
    return enum_key_type, enum_value_type


def _composed_name(descriptor: Descriptor) -> str:
    parent_chain = _ancestor_names(descriptor)
    if not parent_chain:
        namespace = _package_to_namespace(descriptor.file.package)
        return f"{namespace}.{descriptor.name}"
    return ".Types.".join(parent_chain) + ".Types." + descriptor.name


def _parent_name(descriptor: Descriptor) -> str | None:
    parent_chain = _ancestor_names(descriptor)
    if not parent_chain:
        return None
    return ".Types.".join(parent_chain) + ".Types"


def _ancestor_names(descriptor: Descriptor) -> list[str]:
    chain: list[str] = []
    current = descriptor.containing_type
    while current is not None:
        chain.append(current.name)
        current = current.containing_type
    chain.reverse()
    return chain


def _package_to_namespace(package: str) -> str:
    return ".".join(segment[:1].upper() + segment[1:] for segment in package.split(".") if segment)


def _file_descriptor_name(proto_file_name: str) -> str:
    stem = Path(proto_file_name).stem
    pascal = "".join(part[:1].upper() + part[1:] for part in stem.split("_") if part)
    return f"{pascal}Reflection"


def _snake_to_pascal(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


def _snake_to_camel_with_underscore(name: str) -> str:
    pascal = _snake_to_pascal(name)
    if not pascal:
        return name
    return pascal[:1].lower() + pascal[1:] + "_"


def _is_synthetic_oneof(oneof: OneofDescriptor) -> bool:
    if getattr(oneof, "_is_synthetic", False):
        return True
    fields: list[FieldDescriptor] = list(oneof.fields)
    if len(fields) != 1:
        return False
    return oneof.name == f"_{fields[0].name}"
