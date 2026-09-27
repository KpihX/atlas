from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any, cast

import yaml

from .models import NotesDocument, Task, Utterance

SPACE = re.compile(r"\s+")
SOURCE_ID = re.compile(r"\b(?:utt|task|card)_[a-zA-Z0-9]+\b")
UNKNOWN_PARTICIPANT = re.compile(
    r"^(unknown|unidentified|speaker\s*\d*|inconnu|inconnue|allah\s*sp[eé]ci|dieu|allah)\b",
    re.IGNORECASE,
)
LIMITS = {
    "synthesis": 4,
    "participants": 8,
    "topics": 8,
    "findings": 8,
    "ideas": 6,
    "hypotheses": 5,
    "questions": 6,
    "decisions": 6,
    "recommendations": 6,
    "commitments": 6,
    "actions": 0,
    "current_work": 6,
}

HEADINGS = {
    "en": {
        "title": "Living notes",
        "synthesis": "Current synthesis",
        "participants": "Participants",
        "topics": "Topics",
        "findings": "Findings",
        "ideas": "Ideas",
        "hypotheses": "Hypotheses",
        "questions": "Open questions",
        "decisions": "Decisions",
        "recommendations": "Recommendations",
        "commitments": "Commitments",
        "current_work": "Current work",
    },
    "fr": {
        "title": "Notes vivantes",
        "synthesis": "Synthèse actuelle",
        "participants": "Participants",
        "topics": "Sujets",
        "findings": "Résultats",
        "ideas": "Idées",
        "hypotheses": "Hypothèses",
        "questions": "Questions ouvertes",
        "decisions": "Décisions",
        "recommendations": "Recommandations",
        "commitments": "Engagements",
        "current_work": "Travaux en cours",
    },
}


def parse_notes_document(text: str) -> NotesDocument:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json|yaml)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = yaml.safe_load(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("Notes response must be an object")
    return NotesDocument.model_validate(payload)


def normalize_notes(document: NotesDocument, tasks: list[Task]) -> NotesDocument:
    data = document.model_dump()
    data["recommendations"] = [*data.get("recommendations", []), *data.get("actions", [])]
    data["actions"] = []
    for field, limit in LIMITS.items():
        values = data.get(field, [])
        data[field] = _dedupe(values, limit)
    data["participants"] = [item for item in data["participants"] if not UNKNOWN_PARTICIPANT.match(item)]
    data["current_work"] = _task_truth(tasks)
    data["source_ids"] = list(dict.fromkeys(document.source_ids))[-500:]
    return NotesDocument.model_validate(data)


def integrate_sources(document: NotesDocument, turns: Iterable[Utterance]) -> NotesDocument:
    source_ids = list(document.source_ids)
    source_ids.extend(turn.id for turn in turns)
    return document.model_copy(update={"source_ids": list(dict.fromkeys(source_ids))[-500:]})


def integrate_legacy_sources(document: NotesDocument, legacy_notes: str) -> NotesDocument:
    source_ids = list(document.source_ids)
    source_ids.extend(SOURCE_ID.findall(legacy_notes))
    return document.model_copy(update={"source_ids": list(dict.fromkeys(source_ids))[-500:]})


def integrate_task_finding(document: NotesDocument, task: Task, language: str) -> NotesDocument:
    if task.status != "done" or task.result is None:
        return document
    rows_value: object = task.result.get("sources")
    rows = cast(list[object], rows_value) if isinstance(rows_value, list) else []
    sources = [cast(dict[str, Any], source) for source in rows if isinstance(source, dict)]
    titles = [str(source.get("title", "")).strip() for source in sources[:3]]
    titles = [title for title in titles if title]
    result_count = task.result.get("result_count")
    count = int(result_count) if isinstance(result_count, int | float) else len(sources)
    source_detail = ", ".join(titles)
    if language == "fr":
        finding = f"Recherche terminée : {task.summary}. {count} sources vérifiées"
        if source_detail:
            finding += f", notamment {source_detail}"
    else:
        finding = f"Research completed: {task.summary}. {count} verified sources"
        if source_detail:
            finding += f", including {source_detail}"
    findings = _dedupe([*document.findings, finding + "."], LIMITS["findings"])
    source_ids = list(dict.fromkeys([*document.source_ids, task.id]))[-500:]
    return document.model_copy(update={"findings": findings, "source_ids": source_ids})


def render_notes(document: NotesDocument, language: str) -> str:
    headings = HEADINGS.get(language, HEADINGS["en"])
    sections = [f"# {headings['title']}"]
    if document.synthesis:
        sections.append(f"## {headings['synthesis']}\n\n" + "\n\n".join(document.synthesis))
    for field in (
        "participants",
        "topics",
        "findings",
        "ideas",
        "hypotheses",
        "questions",
        "decisions",
        "recommendations",
        "commitments",
        "current_work",
    ):
        values = getattr(document, field)
        if values:
            sections.append(f"## {headings[field]}\n\n" + "\n".join(f"- {item}" for item in values))
    return "\n\n".join(sections).strip() + "\n"


def _dedupe(values: Iterable[Any], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = SPACE.sub(" ", str(raw)).strip(" -\n\t")
        if not value:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result[:limit]


def _task_truth(tasks: list[Task]) -> list[str]:
    result: list[str] = []
    for task in tasks[-20:]:
        if task.status in {"queued", "running"}:
            result.append(f"{task.summary} ({task.phase})")
        elif task.status == "failed":
            result.append(f"{task.summary} (failed: {task.error or 'unknown error'})")
    return _dedupe(result, LIMITS["current_work"])
