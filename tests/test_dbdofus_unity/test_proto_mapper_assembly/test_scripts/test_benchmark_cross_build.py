from DBDofusUnity.proto_mapper_assembly.interfaces.pinned_pairs import PinnedPair, PinnedPairsConfig
from scripts.mapping.debug.benchmark_cross_build import _pins_for_run, _score_pins
from tests.fixtures.proto_mapper.script_builders import game_mapping_entry


def test_leave_one_pin_out_removes_only_the_selected_pair() -> None:
    pins = PinnedPairsConfig(
        pairs=[
            PinnedPair(obf="obf.First", non_obf=".non.ObfFirst"),
            PinnedPair(obf="obf.Second", non_obf="non.ObfSecond"),
        ]
    )

    remaining = _pins_for_run(pins, ".NON.OBFFIRST")

    assert [pair.non_obf for pair in remaining.pairs] == ["non.ObfSecond"]
    assert len(pins.pairs) == 2


def test_regular_benchmark_keeps_the_original_pin_config() -> None:
    pins = PinnedPairsConfig(pairs=[PinnedPair(obf="obf.First", non_obf="non.ObfFirst")])

    assert _pins_for_run(pins, None) is pins


def test_pin_scores_normalize_names_and_keep_reference_names_for_missing_messages() -> None:
    pins = PinnedPairsConfig(
        pairs=[
            PinnedPair(obf="right", non_obf="Protocol.Present"),
            PinnedPair(obf="gone", non_obf="Protocol.Missing"),
            PinnedPair(obf="expected", non_obf="Protocol.Wrong"),
        ]
    )
    candidate = {
        ".protocol.Present": game_mapping_entry(field_mapping={}, obf_cls="right"),
        ".protocol.Wrong": game_mapping_entry(field_mapping={}, obf_cls="actual"),
    }
    outcomes = _score_pins(
        candidate_document_root=candidate,
        era_pins=pins,
        reference_keys=[".protocol.Missing"],
    )
    assert [outcome.non_obf_key for outcome in outcomes] == [
        ".protocol.Present",
        ".protocol.Missing",
        ".protocol.Wrong",
    ]
    assert [outcome.actual_obf for outcome in outcomes] == ["right", None, "actual"]
    assert [outcome.is_recovered for outcome in outcomes] == [True, False, False]
