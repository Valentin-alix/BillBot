from dataclasses import dataclass
from typing import Literal

from DBDofusUnity.proto_mapper_assembly.interfaces.assembly_access import FieldAccessEntry


@dataclass(frozen=True, slots=True)
class NumericFieldValue:
    cls: str
    field_name: str | None
    property_name: str | None
    field_offset: int | None
    access_kind: Literal["read", "getter"]
    instruction_address: int
    index_in_function: int
    width: Literal[8, 16, 32, 64]
    adjustment: int = 0

    def access_entry(self) -> FieldAccessEntry:
        return FieldAccessEntry(
            type="field",
            access_kind=self.access_kind,
            cls=self.cls,
            field_name=self.field_name,
            property_name=self.property_name,
            field_offset=self.field_offset,
            instruction_address=self.instruction_address,
            index_in_function=self.index_in_function,
        )


@dataclass(frozen=True, slots=True)
class NumericComparison:
    value: NumericFieldValue
    constant: int
    instruction_address: int
    reversed_operands: bool = False
