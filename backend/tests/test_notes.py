import json
from pathlib import Path

from atlas.core.models import NotesDocument, Task, Utterance
from atlas.core.notes import integrate_sources, integrate_task_finding, normalize_notes, render_notes


def test_notes_keep_provenance_out_of_human_rendering() -> None:
    document = NotesDocument(
        synthesis=["The team is evaluating an emergency-response project."],
        source_ids=["utt_old"],
    )
    updated = integrate_sources(document, [Utterance(id="utt_new", text="New idea", source="manual")])
    rendered = render_notes(updated, "en")
    assert updated.source_ids == ["utt_old", "utt_new"]
    assert "utt_old" not in rendered
    assert "utt_new" not in rendered


def test_notes_are_deduplicated_bounded_and_section_owned() -> None:
    document = NotesDocument(
        synthesis=["One theme.", "One theme."],
        participants=["Unknown participant", "Pavel", "Pavel"],
        topics=[f"Topic {index}" for index in range(12)],
        questions=["What data exists?", "What data exists?"],
        current_work=["invented work"],
    )
    tasks = [Task(tool="exa_search", summary="Research verified sources", status="running")]
    normalized = normalize_notes(document, tasks)
    assert normalized.synthesis == ["One theme."]
    assert normalized.participants == ["Pavel"]
    assert len(normalized.topics) == 8
    assert normalized.questions == ["What data exists?"]
    assert normalized.current_work == ["Research verified sources (executing)"]


def test_notes_can_shrink_when_understanding_improves() -> None:
    verbose = NotesDocument(topics=[f"Repeated fragment {index}" for index in range(8)])
    concise = NotesDocument(synthesis=["The fragments resolve into one durable idea."])
    assert len(render_notes(concise, "en")) < len(render_notes(verbose, "en"))


def test_real_session_regression_fixture_stays_concise_and_section_owned() -> None:
    fixture = Path(__file__).parent / "fixtures" / "notes-regressions.json"
    document = NotesDocument.model_validate(json.loads(fixture.read_text()))
    tasks = [Task(tool="exa_search", summary="Verify Nepal flood facts", status="done")]
    normalized = normalize_notes(document, tasks)
    rendered = render_notes(normalized, "en")
    assert normalized.participants == ["Pavel"]
    assert len(normalized.topics) <= 8
    assert len(normalized.questions) <= 6
    assert normalized.recommendations.count("Verify the Nepal flood facts.") == 1
    assert normalized.current_work == []
    assert "Unknown participant" not in rendered
    assert "Research is pending forever" not in rendered
    assert "utt_nepal" not in rendered


def test_recommendations_never_become_commitments_without_explicit_agreement() -> None:
    document = NotesDocument(
        findings=["External evidence shows a validation gap."],
        ideas=["Explore an education-domain adaptation."],
        actions=["Consult domain experts."],
        commitments=[],
    )
    normalized = normalize_notes(document, [])
    assert normalized.findings == ["External evidence shows a validation gap."]
    assert normalized.ideas == ["Explore an education-domain adaptation."]
    assert normalized.recommendations == ["Consult domain experts."]
    assert normalized.commitments == []
    assert normalized.actions == []


def test_completed_grounded_task_becomes_a_bounded_finding() -> None:
    document = NotesDocument()
    task = Task(
        tool="exa_search",
        summary="verify the official flood alert source",
        status="done",
        result={
            "result_count": 5,
            "sources": [
                {"title": "Official Flood Monitoring", "url": "https://example.test/flood"},
                {"title": "River Watch", "url": "https://example.test/river"},
            ],
        },
    )

    integrated = integrate_task_finding(document, task, "en")
    repeated = integrate_task_finding(integrated, task, "en")

    assert len(repeated.findings) == 1
    assert "5 verified sources" in repeated.findings[0]
    assert task.id in repeated.source_ids
