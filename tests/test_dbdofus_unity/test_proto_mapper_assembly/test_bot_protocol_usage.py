from pathlib import Path
from textwrap import dedent

import pytest
from google.protobuf import descriptor_pb2

from DBDofusUnity.proto_mapper_assembly.controllers.bot_protocol_usage import (
    ProtocolUsageError,
    generate_auto_mode_mapping_contract,
)


@pytest.fixture
def usage_project(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "src"
    protos = tmp_path / "protos"
    source.mkdir()
    protos.mkdir()
    file = descriptor_pb2.FileDescriptorProto(name="bot_usage_fixture.proto", package="Game", syntax="proto3")
    root = file.message_type.add(name="Root")
    child = root.nested_type.add(name="Child")
    child.field.add(name="id", number=1, type=5)
    child.field.add(name="name", number=2, type=9)
    root.field.add(name="child", number=1, type=11, type_name=".Game.Root.Child")
    root.field.add(name="children", number=2, type=11, type_name=".Game.Root.Child", label=3)
    root.field.add(name="title", number=3, type=9)
    entry = root.nested_type.add(name="ByNameEntry")
    entry.options.map_entry = True
    entry.field.add(name="key", number=1, type=9)
    entry.field.add(name="value", number=2, type=11, type_name=".Game.Root.Child")
    root.field.add(name="by_name", number=4, type=11, type_name=".Game.Root.ByNameEntry", label=3)
    root.oneof_decl.add(name="choice")
    root.field.add(name="selected_text", number=5, type=9, oneof_index=0)
    root.field.add(name="selected_child", number=6, type=11, type_name=".Game.Root.Child", oneof_index=0)
    file.message_type.add(name="Event")
    file.message_type.add(name="Unused")
    (protos / "bot_usage_fixture_pb2.py").write_text(
        "from google.protobuf.descriptor_pool import Default\n"
        f"DESCRIPTOR = Default().AddSerializedFile({file.SerializeToString()!r})\n",
        encoding="utf-8",
    )
    return source, protos


def _requirements(project: tuple[Path, Path], code: str) -> dict[str, list[str]]:
    source, protos = project
    (source / "consumer.py").write_text(dedent(code), encoding="utf-8")
    contract = generate_auto_mode_mapping_contract(source_root=source, protos_dir=protos)
    return {message.message: message.fields for message in contract.messages}


def test_tracks_aliases_constructors_presence_and_nested_fields(usage_project: tuple[Path, Path]) -> None:
    requirements = _requirements(usage_project, """
        from bot_usage_fixture_pb2 import Root as Request, Event, Unused
        def receive(message: Request):
            message.HasField("child")
            message.child.id = 1
            return message.child.name
        request = Request(title="hello", child=Request.Child(id=1))
        register(Event)
    """)
    assert requirements == {
        ".game.Event": [],
        ".game.Root": ["child", "title"],
        ".game.Root.Child": ["id", "name"],
    }


def test_propagates_through_state_inheritance_helpers_returns_and_collections(usage_project: tuple[Path, Path]) -> None:
    source, _ = usage_project
    (source / "helper.py").write_text("def identity(value):\n    return value\n", encoding="utf-8")
    requirements = _requirements(usage_project, """
        from bot_usage_fixture_pb2 import Root
        from src.helper import identity
        class State:
            saved: list[Root.Child]
        class Base:
            state: State
        class Consumer(Base):
            def receive(self, message: Root):
                self.state.saved = list(message.children)
                for item in self.state.saved:
                    use(identity(item))
                for key, value in message.by_name.items():
                    value.id
                message.by_name["key"].name
        def use(value):
            return value.name
    """)
    assert requirements == {
        ".game.Root": ["by_name", "children"],
        ".game.Root.Child": ["id", "name"],
    }


def test_tracks_comprehensions_match_branches_and_closures(usage_project: tuple[Path, Path]) -> None:
    requirements = _requirements(usage_project, """
        from bot_usage_fixture_pb2 import Root
        def receive(message: Root):
            def later():
                return message.child.name
            values = [item.id for item in message.children]
            match len(values):
                case 0:
                    return Root(title="empty")
                case _:
                    return later()
    """)
    assert requirements[".game.Root"] == ["child", "children", "title"]
    assert requirements[".game.Root.Child"] == ["id", "name"]


def test_tracks_generic_message_helpers_and_literal_reflection(usage_project: tuple[Path, Path]) -> None:
    requirements = _requirements(usage_project, """
        from bot_usage_fixture_pb2 import Root
        from google.protobuf.message import Message
        def helper(value: Message):
            return getattr(value, "title")
        def receive(message: Root):
            helper(message)
            setattr(message.child, "id", 4)
            message.ClearField("children")
    """)
    assert requirements[".game.Root"] == ["child", "children", "title"]
    assert requirements[".game.Root.Child"] == ["id"]


def test_tracks_oneofs_and_whole_message_copy(usage_project: tuple[Path, Path]) -> None:
    requirements = _requirements(usage_project, """
        from bot_usage_fixture_pb2 import Root
        def receive(message: Root):
            selected = message.WhichOneof("choice")
            getattr(message, selected)
            message.child.CopyFrom(Root.Child())
    """)
    assert requirements[".game.Root"] == ["child", "selected_child", "selected_text"]
    assert requirements[".game.Root.Child"] == ["id", "name"]


def test_tracks_mutated_collections_and_async_returns(usage_project: tuple[Path, Path]) -> None:
    requirements = _requirements(usage_project, """
        from bot_usage_fixture_pb2 import Root
        async def identity(value):
            return value
        async def receive(message: Root):
            saved = []
            saved.append(await identity(message.child))
            by_key = {}
            by_key["first"] = message.child
            for item in saved:
                item.id
            for item in by_key.values():
                item.name
    """)
    assert requirements[".game.Root"] == ["child"]
    assert requirements[".game.Root.Child"] == ["id", "name"]


def test_rejects_generic_receiver_without_a_resolvable_concrete_type(usage_project: tuple[Path, Path]) -> None:
    with pytest.raises(ProtocolUsageError, match="Unresolved protobuf receiver"):
        _requirements(usage_project, """
            from google.protobuf.message import Message
            def receive(message: Message):
                return message.HasField("title")
        """)


def test_only_excludes_identified_transport_reflection_functions(usage_project: tuple[Path, Path]) -> None:
    source, _ = usage_project
    protocol = source / "protocol"
    protocol.mkdir()
    (protocol / "protocol_game.py").write_text(dedent("""
        from bot_usage_fixture_pb2 import Root
        from google.protobuf.message import Message
        def set_field_from_mapping_field(output_msg: Message, name):
            getattr(output_msg, name)
        def ordinary_consumer(message: Root):
            message.child.id
    """), encoding="utf-8")
    requirements = _requirements(usage_project, "")
    assert requirements == {".game.Root": ["child"], ".game.Root.Child": ["id"]}
    with pytest.raises(ProtocolUsageError, match="Unresolved dynamic protobuf field name"):
        _requirements(usage_project, """
            from bot_usage_fixture_pb2 import Root
            def business_consumer(message: Root, name):
                return getattr(message, name)
        """)


@pytest.mark.parametrize("expression", [
    "getattr(message, field_name)",
    "message.HasField(field_name)",
    "message.missing_field",
    "message.WhichOneof('missing_group')",
    "Root(**values)",
])
def test_rejects_unresolved_or_invalid_fields_with_location(
    usage_project: tuple[Path, Path], expression: str,
) -> None:
    with pytest.raises(ProtocolUsageError, match=r"consumer.py:4:"):
        _requirements(usage_project, f"""
            from bot_usage_fixture_pb2 import Root
            def receive(message: Root, field_name: str, values: dict):
                {expression}
        """)


def test_does_not_execute_application_code_or_include_connection_protocol(usage_project: tuple[Path, Path]) -> None:
    requirements = _requirements(usage_project, """
        from bot_usage_fixture_pb2 import Event
        from DBDofusUnity.datas.protos.non_obf.connection.login_message_pb2 import Request
        register(Event)
        Request()
        raise RuntimeError("Application code must not execute")
    """)
    assert requirements == {".game.Event": []}


def test_current_bot_contract_is_deterministic_and_includes_nested_usage() -> None:
    first = generate_auto_mode_mapping_contract()
    second = generate_auto_mode_mapping_contract()
    assert first == second
    requirements = {message.message: message.fields for message in first.messages}
    prefix = ".com.ankama.dofus.server.game.protocol."
    assert {"agility", "chance", "intelligence", "strength"} <= set(
        requirements[prefix + "character.CharacterCharacteristicUpgradeRequest"]
    )
    assert "object_uid" in requirements[prefix + "inventory.ObjectUseRequest"]
    assert {"characteristics", "spell_modifiers"} <= set(requirements[prefix + "common.CharacterCharacteristics"])
    assert "monster_gid" in requirements[prefix + "game.action.EntitySpawnInformation.Monster"]
    assert prefix + "common.CharacterInformation" not in requirements
    assert "search_by_character_name" not in requirements.get(prefix + "common.PlayerSearch", [])
