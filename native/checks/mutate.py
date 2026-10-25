#!/usr/bin/env python3
"""Run declared native-workspace mutants in a disposable source/build copy."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from process_check import Json, Object, SetupError, digest, object_value, parse_object, rows, source_hashes, string_value


class Options(argparse.Namespace):
    source: Path
    baseline_report: Path
    plan: Path
    evidence: Path
    target_dir: Path | None


@dataclass(frozen=True, slots=True)
class MutationEdit:
    path: str
    pattern: str
    replacement: str


@dataclass(frozen=True, slots=True)
class Mutation:
    id: str
    test: str
    expected_assertion: str
    edits: tuple[MutationEdit, ...]


def mutation_plan(data: Object) -> tuple[Mutation, ...]:
    mutations: list[Mutation] = []
    for row in rows(data.get("mutations"), "mutations"):
        edits = tuple(
            MutationEdit(
                string_value(edit.get("path"), "mutation path"),
                string_value(edit.get("pattern"), "mutation pattern"),
                string_value(edit.get("replacement"), "mutation replacement"),
            )
            for edit in rows(row.get("edits"), "mutation edits")
        )
        mutations.append(
            Mutation(
                string_value(row.get("id"), "mutation id"),
                string_value(row.get("test"), "mutation test"),
                string_value(row.get("expected_assertion"), "mutation assertion"),
                edits,
            )
        )
    return tuple(mutations)


def hashes(native: Path) -> dict[str, str]:
    result = source_hashes(native)
    result.update({str(path.relative_to(native)): digest(path) for path in sorted((native / "core" / "tests").glob("*.rs"))})
    return result


def execute(command: list[str], directory: Path, label: str, seconds: int = 900) -> subprocess.CompletedProcess[str]:
    stage: Object = {"case": directory.name, "stage": label}
    print(json.dumps(stage), flush=True)
    environment: dict[str, str] = {**os.environ, "CARGO_TERM_COLOR": "never", "RUST_BACKTRACE": "0"}
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            timeout=seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise SetupError(f"{label} timed out; timeout is not a mutation kill") from error
    (directory / f"{label}.stdout.log").write_text(result.stdout, encoding="utf-8")
    (directory / f"{label}.stderr.log").write_text(result.stderr, encoding="utf-8")
    return result


def build(native: Path, target: Path, directory: Path) -> Path:
    manifest = native / "Cargo.toml"
    if not manifest.exists():
        manifest = native / "core" / "Cargo.toml"
    # This target belongs only to the disposable campaign. Clean the package
    # before every build so restoring/copying sources cannot reuse a mutant.
    clean = execute(["cargo", "clean", "--manifest-path", str(manifest), "-p", "workspace-core", "--target-dir", str(target)], directory, "clean")
    if clean.returncode:
        raise SetupError("Disposable package clean failed")
    result = execute(
        [
            "cargo",
            "test",
            "--locked",
            "--manifest-path",
            str(manifest),
            "-p",
            "workspace-core",
            "--test",
            "store",
            "--no-run",
            "--message-format=json",
            "--target-dir",
            str(target),
            "-j",
            "2",
        ],
        directory,
        "build",
    )
    if result.returncode:
        raise SetupError("Mutant/baseline build failed; compilation failure is not a mutation kill")
    executables: list[Path] = []
    for line in result.stdout.splitlines():
        try:
            row = parse_object(line, "Cargo output")
        except json.JSONDecodeError:
            continue
        if row.get("reason") != "compiler-artifact":
            continue
        artifact = object_value(row.get("target", {}), "Cargo target")
        if artifact.get("name") == "store" and row.get("executable"):
            executables.append(Path(string_value(row["executable"], "Store test executable")))
    if len(set(executables)) != 1:
        raise SetupError("Build did not identify exactly one Store test executable")
    return executables[0]


def run_mutation(native: Path, copied: Path, target: Path, private: Path, *, mutation: Mutation, originals: dict[str, str]) -> Object:
    name = mutation.id
    directory = private / name
    directory.mkdir()
    edits: list[Json] = []
    result: Object = {"id": name, "test": mutation.test, "expected_assertion": mutation.expected_assertion, "edits": edits}
    try:
        for relative, content in originals.items():
            (copied / relative).write_text(content, encoding="utf-8")
        for edit in mutation.edits:
            path = copied / edit.path
            before = path.read_text(encoding="utf-8")
            matches = list(re.finditer(edit.pattern, before))
            if len(matches) != 1:
                raise SetupError(f"Mutation site count is {len(matches)} for {edit.path}")
            after = re.sub(edit.pattern, edit.replacement, before)
            if after == before:
                raise SetupError("Mutation did not change its source")
            path.write_text(after, encoding="utf-8")
            edits.append(
                {
                    "path": edit.path,
                    "before": matches[0].group(),
                    "after": matches[0].expand(edit.replacement),
                    "source_before_sha256": digest(native / edit.path),
                    "mutant_sha256": digest(path),
                }
            )
        executable = build(copied, target, directory)
        result["binary_sha256"] = digest(executable)
        command = [str(executable), "--exact", mutation.test, "--nocapture"]
        result["command"] = [*command]
        observed = execute(command, directory, "test", 180)
        output = observed.stdout + observed.stderr
        intended = observed.returncode != 0 and mutation.expected_assertion in output and f"{mutation.test} ... FAILED" in observed.stdout
        result["outcome"] = "detected" if intended else "survived" if observed.returncode == 0 else "unrelated_failure"
        result["returncode"] = observed.returncode
        result["stdout_sha256"] = digest(directory / "test.stdout.log")
        result["stderr_sha256"] = digest(directory / "test.stderr.log")
        result["clean_stdout_sha256"] = digest(directory / "clean.stdout.log")
        result["clean_stderr_sha256"] = digest(directory / "clean.stderr.log")
        result["intended_assertion_observed"] = intended
    except Exception as error:
        result["outcome"] = "setup_failure"
        result["detail"] = str(error)
    return result


def prepare_copy(native: Path, copied: Path, target: Path, original: dict[str, str]) -> None:
    if target == native or native in target.parents:
        raise SetupError("Mutation target must be outside the production native tree")
    owner = target / ".workspace-mutation-target.json"
    if target.exists() and any(target.iterdir()) and not owner.is_file():
        raise SetupError("Existing target lacks campaign ownership; refusing to affect another build")
    target.mkdir(parents=True, exist_ok=True)
    ownership: Object = {"purpose": "workspace mutation campaigns only"}
    owner.write_text(json.dumps(ownership) + "\n", encoding="utf-8")
    # Creating our ownership marker before Cargo's first build means Cargo does
    # not initialize its own cache tag. Establish the standard tag only after
    # confirming this is a new or already campaign-owned disposable directory.
    (target / "CACHEDIR.TAG").write_text(
        "Signature: 8a477f597d28d172789f06886806bc55\n# Disposable workspace mutation build cache.\n", encoding="utf-8"
    )
    ignore = cast(Callable[[str, list[str]], set[str]], shutil.ignore_patterns("node_modules", "target", "dist", "__pycache__", ".git"))
    shutil.copytree(native, copied, ignore=ignore)
    if hashes(copied) != original:
        raise SetupError("Disposable source copy differs from frozen source")


def parse_options() -> Options:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path(__file__).with_name("mutations.json"))
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path)
    return parser.parse_args(namespace=Options())


def main() -> int:
    options = parse_options()
    native, evidence = options.source.resolve(), options.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    original = hashes(native)
    mutations: list[Object] = []
    report: Object = {
        "kind": "native-workspace-targeted-mutations-v1",
        "passed": False,
        "source_hashes": {**original},
        "plan_sha256": digest(options.plan),
        "harness_hashes": {name: digest(Path(__file__).with_name(name)) for name in ("mutate.py", "process_check.py")},
        "baseline_report_sha256": digest(options.baseline_report),
        "python": sys.version,
        "policy": "Only the named behavioral assertion in the selected test counts. No compiler, setup, timeout or unrelated failure counts.",
    }
    private = Path(tempfile.mkdtemp(prefix="workspace-mutation-campaign-"))
    copied = private / "native"
    target = options.target_dir.resolve() if options.target_dir else private / "target"
    try:
        baseline = parse_object(options.baseline_report.read_text(encoding="utf-8"), "process baseline")
        if any(baseline.get(field) is not True for field in ("passed", "source_unchanged", "binary_unchanged")):
            raise SetupError("A passing, unchanged process baseline is required")
        if baseline.get("harness_sha256") != digest(Path(__file__).with_name("process_check.py")):
            raise SetupError("Process harness differs from the passing baseline")
        for relative, expected in object_value(baseline.get("source_hashes"), "baseline source hashes").items():
            if original.get(relative) != string_value(expected, "baseline source hash"):
                raise SetupError(f"Native source differs from process baseline: {relative}")
        prepare_copy(native, copied, target, original)
        plan = mutation_plan(parse_object(options.plan.read_text(encoding="utf-8"), "mutation plan"))
        originals: dict[str, str] = {}
        for mutation in plan:
            for edit in mutation.edits:
                relative_path = Path(edit.path)
                if relative_path.parts[:2] != ("core", "src") or ".." in relative_path.parts or relative_path.suffix != ".rs":
                    raise SetupError("Mutation plan must only change Rust implementation source")
                originals[edit.path] = (copied / relative_path).read_text(encoding="utf-8")
        baseline_dir = private / "baseline"
        baseline_dir.mkdir()
        executable = build(copied, target, baseline_dir)
        baseline_result = execute([str(executable), "--nocapture"], baseline_dir, "test", 180)
        passed_tests = re.search(r"test result: ok\. ([1-9][0-9]*) passed; 0 failed;", baseline_result.stdout)
        if baseline_result.returncode or passed_tests is None:
            raise SetupError("Unmodified public Store tests failed in the disposable build")
        report["store_baseline"] = {"passed": True, "tests_passed": int(passed_tests.group(1)), "binary_sha256": digest(executable)}
        for mutation in plan:
            mutations.append(run_mutation(native, copied, target, private, mutation=mutation, originals=originals))
        report["passed"] = bool(mutations) and all(row["outcome"] == "detected" for row in mutations)
    except Exception as error:
        report["failure"] = {"type": "setup", "detail": str(error)}
    finally:
        report["mutations"] = [*mutations]
        report["source_unchanged"] = original == hashes(native)
        if not report["source_unchanged"]:
            report["passed"] = False
            report["failure"] = {"type": "setup", "detail": "Live source changed during the campaign"}
        report["private_logs_root"] = str(private)
        (evidence / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary: Object = {
        "passed": report["passed"],
        "outcomes": [{"id": row["id"], "outcome": row["outcome"]} for row in mutations],
        "failure": report.get("failure"),
        "report": str(evidence / "report.json"),
    }
    print(json.dumps(summary, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
