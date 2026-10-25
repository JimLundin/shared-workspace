"""Real read handles distinguish verified retries from required binding commits."""

import os
import sys
import threading
from pathlib import Path
from unittest import SkipTest, TestCase

if sys.platform != "win32":
    raise SkipTest("Windows draft binding sharing")

from assistant.outlook import DraftConflict, DraftUncertain, SavedEmlDraft, SavedOutlookDraft, draft_key, eml_bytes

from .test_outlook import adapter, request


def test_pending_bindings_stay_uncertain_when_a_reader_blocks_the_required_ready_commit(tmp_path: Path) -> None:
    for mode in ("unavailable", "normal"):
        drafts, command = adapter(tmp_path / mode)
        original = request()
        if mode == "unavailable":
            command.configure("unavailable")
            eml = drafts.folder / "drafts" / (draft_key(original) + ".eml")
            eml.mkdir(parents=True)
            assert isinstance(drafts.save(original, threading.Event()), DraftUncertain)
            eml.rmdir()
            eml.write_bytes(eml_bytes(original))
        else:
            command.configure("lose_ack")
            assert isinstance(drafts.save(original, threading.Event()), DraftUncertain)
            command.configure()
        index = drafts.folder / "index" / (draft_key(original) + ".json")
        pending = index.read_bytes()
        observed = drafts.inspect(original, threading.Event())
        assert isinstance(observed, (SavedEmlDraft, SavedOutlookDraft)), observed
        with index.open("rb") as reader:
            identity = os.fstat(reader.fileno())
            probe = tmp_path / f"{mode}-replacement-probe"
            probe.write_bytes(pending)
            try:
                with TestCase().assertRaises(PermissionError) as raised:
                    probe.replace(index)
                assert raised.exception.winerror in (5, 32, 33)
            finally:
                probe.unlink(missing_ok=True)
            assert drafts.inspect(original, threading.Event()) == observed
            result = drafts.save(original, threading.Event())
            assert isinstance(result, DraftUncertain), result
            assert index.read_bytes() == pending and os.path.samestat(identity, index.stat())
            assert drafts.inspect(original, threading.Event()) == observed
        committed = drafts.save(original, threading.Event())
        assert committed == observed, committed
        artifact = observed.path if isinstance(observed, SavedEmlDraft) else command.files()[0]
        artifact.unlink()
        assert isinstance(drafts.save(original, threading.Event()), DraftConflict)
        assert not artifact.exists()
