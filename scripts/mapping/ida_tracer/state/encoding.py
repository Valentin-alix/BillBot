def encode_field_address(class_name: str, offset: int) -> str:
    return f"{class_name}\x00{offset}"


def decode_field_address(encoded: str) -> tuple[str, int]:
    cls, off = encoded.split("\x00", 1)
    return cls, int(off)


def encode_object_offset(class_name: str, offset: int) -> str:
    return f"{class_name}\x00{offset}"


def decode_object_offset(encoded: str) -> tuple[str, int]:
    cls, off = encoded.split("\x00", 1)
    return cls, int(off)
