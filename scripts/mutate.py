"""Run focused decision mutations in disposable source copies (requires pytest).

Example: python scripts/mutate.py assistant/recording_state.py --tests tests/test_recording_state.py
The checkout is never modified. Timeouts and collection errors are not kills.
"""

import argparse
import ast
import hashlib
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
type Status = Literal["killed", "survived", "timeout", "error"]


@dataclass(frozen=True, slots=True)
class Mutation:
    function: str
    line: int
    start: int
    end: int
    before: str
    after: str

    def apply(self, source: bytes) -> bytes:
        return source[:self.start] + self.after.encode("utf-8") + source[self.end:]


@dataclass(frozen=True, slots=True)
class Result:
    status: Status
    seconds: float
    tests: int
    killed_by: tuple[str, ...] = ()
    detail: str = ""


def expressions(tree: ast.AST, function: str = "") -> Iterator[tuple[str, ast.expr, str]]:
    """Mutate runtime decisions, not annotations, schemas, docstrings, or messages."""
    if isinstance(tree, ast.ClassDef):
        for statement in tree.body:
            yield from expressions(statement, f"{function}.{tree.name}".strip("."))
        return
    if isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = f"{function}.{tree.name}".strip(".")
        for statement in tree.body:
            yield from expressions(statement, name)
        return
    if isinstance(tree, (ast.AnnAssign, ast.arg)):
        if isinstance(tree, ast.AnnAssign) and tree.value is not None:
            yield from expressions(tree.value, function)
        return
    if function:
        changed: ast.expr
        if isinstance(tree, (ast.If, ast.IfExp, ast.While)):
            yield function, tree.test, f"(not ({ast.unparse(tree.test)}))"
        elif isinstance(tree, ast.Compare):
            swaps: dict[type[ast.cmpop], type[ast.cmpop]] = {
                ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
                ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
                ast.In: ast.NotIn, ast.NotIn: ast.In,
            }
            for index, operator in enumerate(tree.ops):
                operators = list(tree.ops)
                operators[index] = swaps[type(operator)]()
                changed = ast.Compare(left=tree.left, ops=operators, comparators=tree.comparators)
                yield function, tree, f"({ast.unparse(changed)})"
        elif isinstance(tree, ast.BoolOp):
            changed = ast.BoolOp(op=ast.Or() if isinstance(tree.op, ast.And) else ast.And(), values=tree.values)
            yield function, tree, f"({ast.unparse(changed)})"
        elif isinstance(tree, ast.BinOp):
            arithmetic: dict[type[ast.operator], type[ast.operator]] = {
                ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Mult,
            }
            if replacement := arithmetic.get(type(tree.op)):
                changed = ast.BinOp(left=tree.left, op=replacement(), right=tree.right)
                yield function, tree, f"({ast.unparse(changed)})"
        elif isinstance(tree, ast.Constant) and type(tree.value) in (int, float):
            value = cast(int | float, tree.value)
            yield function, tree, repr(value + 1)
        elif isinstance(tree, ast.Constant) and isinstance(tree.value, bool):
            yield function, tree, str(not tree.value)
    for child in ast.iter_child_nodes(tree):
        yield from expressions(child, function)


def mutations(source: bytes, functions: list[str]) -> list[Mutation]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    found: list[Mutation] = []
    seen: set[tuple[int, int, str]] = set()
    for function, node, replacement in expressions(ast.parse(source)):
        if functions and function not in functions:
            continue
        assert node.end_lineno is not None and node.end_col_offset is not None
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        identity = start, end, replacement
        if identity in seen:
            continue
        seen.add(identity)
        mutation = Mutation(function, node.lineno, start, end, source[start:end].decode("utf-8"), replacement)
        try:
            compile(mutation.apply(source), "<mutation>", "exec")
        except SyntaxError:
            continue  # e.g. a literal used in a match pattern cannot become an expression
        found.append(mutation)
    return sorted(found, key=mutation_order)


def mutation_order(mutation: Mutation) -> tuple[int, int, str]:
    return mutation.line, mutation.start, mutation.after


def replacements(source: bytes, changes: list[list[str]]) -> list[Mutation]:
    found: list[Mutation] = []
    for before, after in changes:
        fragment = before.encode("utf-8")
        if not fragment or source.count(fragment) != 1:
            raise ValueError(f"A seeded replacement must match exactly once: {before!r}")
        start = source.index(fragment)
        mutation = Mutation("seeded", source[:start].count(b"\n") + 1, start, start + len(fragment), before, after)
        compile(mutation.apply(source), "<mutation>", "exec")
        found.append(mutation)
    return found


def copy_source(source: Path, target: Path) -> None:
    """Prune dependencies and generated artifacts before descending."""
    def ignored(_folder: str, names: list[str]) -> set[str]:
        return {name for name in names if name.startswith(".") or name in ("__pycache__", "dist") or name.endswith(".egg-info")}

    shutil.copytree(source, target, ignore=ignored)


def stop_process_tree(process: subprocess.Popen[str]) -> None:
    """A mutant can hang after spawning children; terminate its isolated tree."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, timeout=10, check=False,
        )
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        process.kill()


def timeout_detail(error: subprocess.TimeoutExpired) -> str:
    def decoded(output: bytes | str | None) -> str:
        return output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output or ""

    return (decoded(error.stdout) + decoded(error.stderr))[-4000:]


def run(root: Path, source: Path, content: bytes, tests: list[str], timeout: float) -> Result:
    with tempfile.TemporaryDirectory(prefix="toolkit-mutation-") as scratch:
        folder = Path(scratch)
        candidate = folder / "source"
        copy_source(root, candidate)
        (candidate / source).write_bytes(content)
        report = folder / "results.xml"
        environment = dict(os.environ, PYTHONPATH=str(candidate), PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        environment.pop("TOOLKIT_E2E", None)
        environment.pop("PYTEST_ADDOPTS", None)
        started = time.perf_counter()
        with subprocess.Popen(
            [sys.executable, "-B", "-m", "pytest", "-q", "--tb=short", "-o", "addopts=", f"--junitxml={report}", *tests],
            cwd=candidate, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", start_new_session=sys.platform != "win32",
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                stop_process_tree(process)
                process.communicate(timeout=10)
                return Result("timeout", time.perf_counter() - started, 0, detail=timeout_detail(error))
            returncode = process.wait()
        elapsed = time.perf_counter() - started
        detail = (stdout + stderr)[-4000:]
        if not report.is_file():
            return Result("error", elapsed, 0, detail=detail)
        try:
            cases = list(ElementTree.parse(report).iter("testcase"))
        except ElementTree.ParseError:
            return Result("error", elapsed, 0, detail=detail)
        executed = [case for case in cases if case.find("skipped") is None]
        failed = tuple(f"{case.get('classname')}.{case.get('name')}" for case in cases if case.find("failure") is not None)
        if any(case.find("error") is not None for case in cases) or returncode not in (0, 1) or not executed:
            return Result("error", elapsed, len(executed), detail=detail)
        if returncode == 1 and not failed:
            return Result("error", elapsed, len(executed), detail=detail)
        return Result("killed" if failed else "survived", elapsed, len(executed), failed)


def campaign(
    root: Path, source: Path, original: bytes, selected: list[Mutation], tests: list[str], *, timeout: float, workers: int,
) -> tuple[Result, list[Result], str]:
    # Freeze once: edits made during a long run must not change its test oracle.
    with tempfile.TemporaryDirectory(prefix="toolkit-mutation-baseline-") as scratch:
        snapshot = Path(scratch) / "source"
        copy_source(root, snapshot)
        (snapshot / source).write_bytes(original)
        digest = hashlib.sha256()
        for path in sorted(snapshot.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(snapshot).as_posix().encode() + b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        baseline = run(snapshot, source, original, tests, timeout)
        print(f"Baseline: {baseline.status}, {baseline.tests} tests in {baseline.seconds:.2f}s", flush=True)
        if baseline.status != "survived":
            print(baseline.detail)
            raise SystemExit("Baseline must pass at least one test before mutating.")

        def check(mutation: Mutation) -> Result:
            return run(snapshot, source, mutation.apply(original), tests, timeout)

        results: list[Result] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for mutation, result in zip(selected, pool.map(check, selected), strict=True):
                print(f"{result.status:8} {source}:{mutation.line} {mutation.function}: {mutation.before!r} -> {mutation.after!r}", flush=True)
                results.append(result)
        return baseline, results, digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="one source file, relative to --root")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tests", nargs="+", required=True, help="explicit pytest paths or node IDs")
    parser.add_argument("--function", action="append", default=None, help="qualified function name; repeat to select several")
    parser.add_argument("--replace", nargs=2, action="append", metavar=("BEFORE", "AFTER"), help="seed an exact replacement instead of AST mutations")
    parser.add_argument("--limit", type=int, default=50, help="deterministic sample size; 0 runs every generated mutation")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30, help="seconds per subprocess; timeouts are reported separately")
    parser.add_argument("--workers", type=int, default=1, help="use 1 for tests sharing the Windows desktop")
    parser.add_argument("--output", type=Path, required=True, help="JSON evidence, preferably outside the source tree")
    args = parser.parse_args()
    root = cast(Path, args.root).resolve()
    source = cast(Path, args.source)
    output = cast(Path, args.output).resolve()
    tests = cast(list[str], args.tests)
    limit, seed, workers = cast(int, args.limit), cast(int, args.seed), cast(int, args.workers)
    timeout = cast(float, args.timeout)
    if source.is_absolute() or ".." in source.parts or not (root / source).is_file():
        parser.error("source must be an existing file relative to --root")
    if output.is_relative_to(root) and not any(part.startswith(".") or part == "dist" for part in output.relative_to(root).parts):
        parser.error("put the report outside source folders (for example, dist/mutations.json)")
    if limit < 0 or workers < 1 or timeout <= 0:
        parser.error("limit must be nonnegative; workers and timeout must be positive")
    original = (root / source).read_bytes()
    changes = cast(list[list[str]] | None, args.replace)
    functions = cast(list[str] | None, args.function)
    if changes and functions:
        parser.error("choose either seeded replacements or function selection")
    try:
        candidates = replacements(original, changes) if changes else mutations(original, functions or [])
    except (ValueError, SyntaxError) as error:
        parser.error(str(error))
    if not candidates:
        parser.error("no mutations matched the selected source/functions")
    selected = random.Random(seed).sample(candidates, limit) if 0 < limit < len(candidates) else candidates
    selected.sort(key=mutation_order)
    print(f"Testing {len(selected)} of {len(candidates)} decision mutations in {source}", flush=True)
    baseline, outcomes, digest = campaign(root, source, original, selected, tests, timeout=timeout, workers=workers)
    results: list[dict[str, object]] = [{
        "function": mutation.function, "line": mutation.line, "before": mutation.before, "after": mutation.after,
        "start": mutation.start, "end": mutation.end,
        "status": result.status, "seconds": round(result.seconds, 3), "tests": result.tests,
        "killed_by": result.killed_by, "detail": result.detail,
    } for mutation, result in zip(selected, outcomes, strict=True)]
    output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "source": source.as_posix(), "tests": tests, "generated": len(candidates), "seed": seed,
        "baseline_seconds": baseline.seconds, "baseline_tests": baseline.tests, "results": results,
        "source_sha256": hashlib.sha256(original).hexdigest(), "snapshot_sha256": digest,
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    counts = {status: sum(result["status"] == status for result in results) for status in ("killed", "survived", "timeout", "error")}
    print(", ".join(f"{count} {status}" for status, count in counts.items()), flush=True)
    print(f"Evidence: {output}", flush=True)
    raise SystemExit(0 if counts["killed"] == len(results) else 1)


if __name__ == "__main__":
    main()
