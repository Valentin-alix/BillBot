import sys
from unittest.mock import patch

import pytest
from tests.fixtures.proto_mapper.message_builders import message_signature
from tests.fixtures.proto_mapper.script_builders import (
    single_pair_matching_inputs,
)

import scripts.mapping.debug.inspect_message_pair as script
from DBDofusUnity.proto_mapper_assembly.interfaces.dump_cs_message import DumpCSMessage


class TestSinglePairMatchResultScript:
    @pytest.mark.parametrize("arguments", [[], ["--obf", "obf"], ["--non-obf", "TargetMessage"]])
    def test_requires_both_message_names(self, arguments: list[str]) -> None:
        with pytest.raises(SystemExit) as error:
            script.build_argument_parser().parse_args(arguments)
        assert error.value.code == 2

    def test_main_rejects_an_unknown_non_obf_message(self) -> None:
        obf_message = DumpCSMessage(file_descriptor="fd", name="obf")
        obf_signature = message_signature("obf", declared_field_signatures=[], dump_cs_msg=obf_message)

        with (
            patch.object(
                script,
                "load_matching_inputs",
                return_value=single_pair_matching_inputs(
                    obf_message=obf_message, obf_signature=obf_signature
                ),
            ),
            patch.object(
                sys,
                "argv",
                ["inspect_message_pair.py", "--obf", "obf", "--non-obf", "Com.Ankama.TargetMessage"],
            ),
            pytest.raises(ValueError, match=r"Unknown non-obf message alias: 'Com.Ankama.TargetMessage'"),
        ):
            script.main()
