"""Saved context through public storage and freshness-aware request selection."""

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest import TestCase

from automation.context import ContextStore, Preference, ProjectNote, select_context
from automation.store import TaskStore
from automation.task import RequestSource


def test_reopened_context_selects_relevant_projects_and_keeps_preferences(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    context = ContextStore(path)
    context.save(Preference("language", "Writing style", "Use concise English.", "Jim's saved preference", 10))
    context.save(ProjectNote("atlas", "Atlas", "The Atlas release has a budget review on Friday.", "project/atlas.md", 20, 100))
    context.save(ProjectNote("other", "Orion", "Mira owns the Orion budget review.", "project/orion.md", 21, 100))
    reopened = ContextStore(path)
    selected = select_context(reopened.list(), "Prepare the Atlas budget briefing.", 50)
    description = selected.describe()
    assert "Use concise English." in description
    assert "budget review on Friday" in description and "project/atlas.md" in description
    assert "Mira owns" not in description and "project/orion.md" not in description
    tasks = TaskStore(path)
    task = tasks.submit("request", "Briefing", "Prepare the briefing.", RequestSource("chat"), at=50)
    assert tasks.get(task.id) is not None
    assert len(ContextStore(path).list()) == 3


def test_connective_words_alone_do_not_select_unrelated_project_facts(tmp_path: Path) -> None:
    context = ContextStore(tmp_path / "work.sqlite3")
    context.save(ProjectNote("orion", "Orion", "Mira owns the access request.", "orion/access.md", 10, 100))
    selected = select_context(context.list(), "Prepare the briefing.", 20)
    assert not selected.records and "orion/access.md" not in selected.describe()


def test_expired_and_future_observations_expose_sources_without_stale_facts(tmp_path: Path) -> None:
    context = ContextStore(tmp_path / "work.sqlite3")
    context.save(ProjectNote("past", "Atlas", "The confidential old estimate is 900.", "atlas/estimate.md", 10, 50))
    context.save(ProjectNote("future", "Atlas", "An observation that has not happened yet.", "atlas/future.md", 70, 90))
    fresh = select_context(context.list(), "Atlas", 49)
    assert "confidential old estimate is 900" in fresh.describe()
    expired = select_context(context.list(), "Atlas", 50)
    description = expired.describe()
    assert not expired.records
    assert "atlas/estimate.md" in description and "atlas/future.md" in description
    assert "confidential old estimate is 900" not in description
    assert "has not happened yet" not in description
    assert {record.id for record in expired.stale} == {"past", "future"}


def test_retrieval_does_not_carry_one_project_into_another_request(tmp_path: Path) -> None:
    context = ContextStore(tmp_path / "work.sqlite3")
    context.save(ProjectNote("atlas", "Atlas", "A delivery gate belongs to Atlas.", "atlas/plan.md", 1, 100))
    context.save(ProjectNote("orion", "Orion", "A workshop belongs to Orion.", "orion/plan.md", 1, 100))
    atlas = select_context(context.list(), "Atlas", 10).describe()
    orion = select_context(context.list(), "Orion", 10).describe()
    assert "delivery gate" in atlas and "workshop" not in atlas
    assert "workshop" in orion and "delivery gate" not in orion
    assert len(context.list()) == 2


def test_updated_preferences_reject_older_saves_and_removal_is_durable(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    context = ContextStore(path)
    context.save(Preference("style", "Writing style", "Use detailed prose.", "An earlier request", 10))
    ContextStore(path).save(Preference("style", "Writing style", "Use concise prose.", "The latest request", 20))
    with TestCase().assertRaises(ValueError):
        context.save(Preference("style", "Writing style", "Restore the old preference.", "A stale editor", 15))
    selected = select_context(ContextStore(path).list(), "Draft a reply.", 21).describe()
    assert "Use concise prose." in selected and "Restore" not in selected
    assert context.remove("style")
    assert not ContextStore(path).list()
    assert not context.remove("style")


def test_context_text_and_record_budgets_preserve_complete_available_records(tmp_path: Path) -> None:
    context = ContextStore(tmp_path / "work.sqlite3")
    context.save(Preference("long", "Detailed style", "word " * 799, "A detailed saved preference", 1))
    context.save(ProjectNote("atlas", "Atlas", "Check the signed plan.", "atlas/plan.md", 1, 100))
    context.save(ProjectNote("atlas-two", "Atlas", "Confirm the workshop.", "atlas/workshop.md", 2, 100))
    selected = select_context(context.list(), "Atlas", 10, limit=1, max_chars=800)
    description = selected.describe()
    assert selected.truncated and len(selected.records) == 1
    assert len(description) <= 800
    assert "atlas/workshop.md" in description and "Confirm the workshop." in description
    assert "word word" not in description
    assert len(context.list()) == 3


def test_context_capacity_rejects_new_records_but_allows_existing_edits(tmp_path: Path) -> None:
    context = ContextStore(tmp_path / "work.sqlite3")
    for index in range(200):
        context.save(Preference(str(index), "Preference " + str(index), "A saved preference.", "Jim", 1))
    with TestCase().assertRaises(ValueError):
        context.save(Preference("extra", "Beyond capacity", "Do not discard existing records.", "Jim", 2))
    context.save(Preference("0", "Preference zero", "Updated in place.", "Jim", 3))
    reopened = ContextStore(tmp_path / "work.sqlite3")
    assert len(reopened.list()) == 200
    assert "Updated in place." in select_context(reopened.list(), "", 4).describe()
    assert reopened.get("extra") is None


def test_corrupt_context_is_rejected_instead_of_entering_model_input(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    context = ContextStore(path)
    context.save(ProjectNote("atlas", "Atlas", "A valid recorded fact.", "atlas/notes.md", 10, 20))
    with closing(sqlite3.connect(path)) as external, external:
        external.execute("UPDATE project_context SET expires=? WHERE id=?", ("not a timestamp", "atlas"))
    with TestCase().assertRaises(ValueError):
        select_context(ContextStore(path).list(), "Atlas", 15)


def test_context_payload_validation_precedes_persistence(tmp_path: Path) -> None:
    context = ContextStore(tmp_path / "work.sqlite3")
    with TestCase().assertRaises(ValueError):
        context.save(Preference("empty", "", "Some text.", "Jim", 1))
    with TestCase().assertRaises(ValueError):
        context.save(ProjectNote("backwards", "Atlas", "A note.", "atlas.md", 20, 10))
    with TestCase().assertRaises(ValueError):
        context.save(Preference("unbounded", "Name", "x" * 4001, "Jim", 1))
    assert not context.list()
