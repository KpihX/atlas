from sidecar.core.models import Utterance
from sidecar.core.notes import preserve_notes


def test_notes_never_drop_sourced_history() -> None:
    previous = "# Notes\n\n- Durable fact [utt_old]"
    new_turn = Utterance(id="utt_new", text="A new useful idea", source="manual")
    result = preserve_notes(previous, "# Short rewrite", [new_turn])
    assert "Durable fact [utt_old]" in result
    assert "A new useful idea [utt_new]" in result


def test_complete_candidate_is_kept() -> None:
    previous = "# Notes\n\n- Fact [utt_old]"
    new_turn = Utterance(id="utt_new", text="New", source="manual")
    candidate = "# Notes\n\n- Fact [utt_old]\n- New [utt_new]\n- Added explanation"
    assert preserve_notes(previous, candidate, [new_turn]) == candidate
