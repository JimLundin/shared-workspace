#!/usr/bin/env python3
"""Prove meeting-work guards in disposable sources and an isolated Cargo target.

CARGO_TARGET_DIR, when provided, supplies a cache parent: package artifacts are
written only to its work-mutations child. The live native/browser targets are
never used. Each baseline/variant cleans this copied workspace-core package so
Cargo timestamps cannot reuse a previously mutated library.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


@dataclass(frozen=True)
class Mutation:
    # Explicit slots keep this standalone check runnable on Python 3.8 hosts.
    __slots__ = ("identity", "before", "after", "test", "marker")
    identity: str
    before: str
    after: str
    test: str
    marker: str


MUTATIONS = (
    Mutation("noncausal-source-import", "past.contains(&source.revision_id),", "true,",
             "signed_import_rejects_a_source_outside_the_operations_causal_past", "NONCAUSAL_SOURCE_ACCEPTED"),
    Mutation("other-actor-publishes", "run.id == run_id && run.originating_device == author", "run.id == run_id",
             "source_scope_run_author_and_request_corrections_are_enforced", "other actor cannot publish A run: Applied"),
    Mutation("wrong-run-publishes", "run.id == run_id && run.originating_device == author", "run.originating_device == author",
             "invalid_publications_leave_no_outputs_or_operation_and_stale_heads_conflict", "run: Applied"),
    Mutation("fabricated-quote-publishes", "transcript(projection, op, past, &evidence.source)?.contains(&evidence.quote),",
             "transcript(projection, op, past, &evidence.source)?.contains(&evidence.quote) || true,",
             "invalid_publications_leave_no_outputs_or_operation_and_stale_heads_conflict", "quote: Applied"),
    Mutation("generated-user-evidence", "!generated,", "true,",
             "invalid_publications_leave_no_outputs_or_operation_and_stale_heads_conflict", "generated user provenance: Applied"),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    paths = list((root / "native/core/src").rglob("*.rs"))
    paths += [root / name for name in (
        "native/Cargo.toml", "native/Cargo.lock", "native/core/Cargo.toml",
        "native/core/tests/work.rs", "native/core/scripts/work-mutations.py",
    )]
    return {str(path.relative_to(root)): digest(path) for path in sorted(paths)}


def execute(command: list[str], environment: dict[str, str], timeout: int) -> dict:
    started = time.monotonic()
    child = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             start_new_session=os.name == "posix")
    timed_out = False
    try:
        output, _ = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            os.killpg(child.pid, signal.SIGKILL)
        else:
            child.kill()
        output, _ = child.communicate()
    except BaseException:
        if os.name == "posix":
            os.killpg(child.pid, signal.SIGKILL)
        else:
            child.kill()
        child.communicate()
        raise
    return {"exit_code": child.returncode, "timed_out": timed_out,
            "signal": -child.returncode if child.returncode < 0 else None,
            "seconds": round(time.monotonic() - started, 3), "output": output}


def successful(result: dict) -> bool:
    return result["exit_code"] == 0 and not result["timed_out"] and result["signal"] is None


def intended_failure(result: dict, mutation: Mutation) -> bool:
    output = result["output"]
    return (result["exit_code"] == 101 and not result["timed_out"] and result["signal"] is None
            and mutation.marker in output
            and f"test {mutation.test} ... FAILED" in output
            and re.search(r"test result: FAILED\. 0 passed; 1 failed;", output) is not None
            and "could not compile" not in output
            and "failed to run custom build command" not in output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("native/core/evidence/work-mutations.json"))
    parser.add_argument("--timeout", type=int, default=900, help="Per-command deadline in seconds")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    root = Path(__file__).resolve().parents[3]
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts = report_path.parent / (report_path.stem + "-logs")
    artifacts.mkdir(exist_ok=True)
    private = Path(tempfile.mkdtemp(prefix="workspace-core-mutations-"))
    cache_parent = Path(os.environ.get("CARGO_TARGET_DIR", str(Path(tempfile.gettempdir()) / "workspace-core-mutation-cache"))).resolve()
    target = cache_parent / "work-mutations"
    environment = dict(os.environ, CARGO_TARGET_DIR=str(target))
    report = {"kind": "work-model-mutations-v2", "started_at": time.time(), "passed": False,
              "source_hashes": source_hashes(root), "harness_sha256": digest(Path(__file__)),
              "target_isolation": "Dedicated work-mutations child; package cleaned before every run.",
              "baseline": None, "restored_baseline": None, "mutations": [],
              "scope": "Real native Turso store with signed workspace operations; no browser, provider or external-effect claim."}
    manifest = private / "native/Cargo.toml"
    mutated_file = private / "native/core/src/work.rs"
    original = (root / "native/core/src/work.rs").read_text()
    clean_command = ["cargo", "clean", "--manifest-path", str(manifest), "-p", "workspace-core"]

    def retain(label: str, result: dict) -> dict:
        log = artifacts / (label + ".txt")
        log.write_text(result["output"])
        return {**{key: value for key, value in result.items() if key != "output"},
                "log": str(log.relative_to(report_path.parent)), "log_sha256": digest(log)}

    def check(label: str, test: str | None = None) -> dict:
        cleaned = execute(clean_command, environment, args.timeout)
        clean_receipt = retain(label + "-clean", cleaned)
        if not successful(cleaned):
            raise RuntimeError(f"{label}: package cleanup failed; see {clean_receipt['log']}")
        command = ["cargo", "test", "--locked", "--manifest-path", str(manifest), "-p", "workspace-core", "--test", "work"]
        if test:
            command += [test, "--", "--exact"]
        print(json.dumps({"stage": label, "phase": "test"}), flush=True)
        return execute(command, environment, args.timeout)

    try:
        # Ordinary copies only. Never hardlink source files or target artifacts.
        (private / "native").mkdir()
        for name in ("Cargo.toml", "Cargo.lock"):
            shutil.copy2(root / "native" / name, private / "native" / name)
        for name in ("core", "cli", "app"):
            shutil.copytree(root / "native" / name, private / "native" / name,
                            ignore=shutil.ignore_patterns("target", "node_modules", "evidence", "__pycache__"))
        for path, expected in report["source_hashes"].items():
            if digest(private / path) != expected:
                raise RuntimeError("The copied baseline differs from its captured source: " + path)
        baseline = check("baseline")
        report["baseline"] = retain("baseline", baseline)
        if not successful(baseline) or not re.search(r"test result: ok\. 5 passed; 0 failed;", baseline["output"]):
            raise RuntimeError("Unchanged five-test baseline did not pass")
        for mutation in MUTATIONS:
            if original.count(mutation.before) != 1:
                raise RuntimeError("Ambiguous mutation: " + mutation.identity)
            mutated_file.write_text(original.replace(mutation.before, mutation.after))
            result = check(mutation.identity, mutation.test)
            killed = intended_failure(result, mutation)
            receipt = {"id": mutation.identity, "test": mutation.test, "marker": mutation.marker,
                       "mutated_sha256": digest(mutated_file), **retain(mutation.identity, result),
                       "outcome": "killed-by-intended-assertion" if killed else "survived-or-invalid-failure"}
            report["mutations"].append(receipt)
            print(json.dumps({"id": mutation.identity, "killed": killed}), flush=True)
        mutated_file.write_text(original)
        restored = check("restored-baseline")
        report["restored_baseline"] = retain("restored-baseline", restored)
        report["source_unchanged"] = report["source_hashes"] == source_hashes(root)
        report["passed"] = (successful(restored) and re.search(r"test result: ok\. 5 passed; 0 failed;", restored["output"]) is not None
                            and report["source_unchanged"] and len(report["mutations"]) == len(MUTATIONS)
                            and all(item["outcome"] == "killed-by-intended-assertion" for item in report["mutations"]))
    except Exception as error:
        report["failure"] = str(error)
    except BaseException:
        report["passed"] = False
        report["failure"] = "Campaign interrupted"
        raise
    finally:
        try:
            if manifest.exists():
                cleanup = execute(clean_command, environment, args.timeout)
                report["package_cleanup"] = retain("final-cleanup", cleanup)
                if not successful(cleanup):
                    report["passed"] = False
        except Exception as error:
            report["passed"] = False
            report["package_cleanup_failure"] = str(error)
        try:
            shutil.rmtree(private)
        except Exception as error:
            report["passed"] = False
            report["private_copy_cleanup_failure"] = str(error)
        report["private_copy_removed"] = not private.exists()
        try:
            report["source_unchanged"] = report["source_hashes"] == source_hashes(root)
        except Exception as error:
            report["source_unchanged"] = False
            report["source_verification_failure"] = str(error)
        report["passed"] = bool(report["passed"] and report["source_unchanged"])
        report["finished_at"] = time.time()
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"passed": report["passed"], "report": str(report_path)}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
