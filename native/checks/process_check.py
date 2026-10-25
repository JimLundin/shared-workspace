#!/usr/bin/env python3
"""Exercise independent native workspace hosts through the public JSON CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None
type Object = dict[str, Json]


class Options(argparse.Namespace):
    binary: Path
    source: Path
    evidence: Path


class SetupError(Exception):
    """A broken test environment or protocol is not a mutation detection."""


class BehavioralError(Exception):
    def __init__(self, assertion: str, observed: Json, expected: Json):
        super().__init__(assertion)
        self.assertion = assertion
        self.observed = observed
        self.expected = expected


def json_value(value: object, context: str) -> Json:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [json_value(item, context) for item in cast(list[object], value)]
    if isinstance(value, dict):
        return object_value(cast(dict[object, object], value), context)
    raise SetupError(f"{context} is not a JSON value")


def object_value(value: object, context: str) -> Object:
    if not isinstance(value, dict):
        raise SetupError(f"{context} is not a JSON object")
    fields = cast(dict[object, object], value)
    return {string_value(key, f"{context} key"): json_value(item, context) for key, item in fields.items()}


def parse_object(payload: str, context: str) -> Object:
    value: object = json.loads(payload)
    return object_value(value, context)


def string_value(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise SetupError(f"{context} is not text")
    return value


def rows(value: object, context: str) -> list[Object]:
    if not isinstance(value, list):
        raise SetupError(f"{context} is not a list")
    return [object_value(row, context) for row in cast(list[object], value)]


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def source_hashes(source: Path) -> dict[str, str]:
    paths = [source / "PROTOCOL.md"]
    for crate in (source, source / "core", source / "cli"):
        paths.extend(path for path in (crate / "Cargo.toml", crate / "Cargo.lock") if path.exists())
    paths.extend((source / "core" / "src").rglob("*.rs"))
    paths.extend((source / "cli" / "src").rglob("*.rs"))
    return {str(path.relative_to(source)): digest(path) for path in sorted(set(paths))}


@dataclass(frozen=True, slots=True)
class Ready:
    device_id: str
    pid: int


class Evidence:
    def __init__(self, path: Path):
        path.mkdir(parents=True, exist_ok=False)
        self.path = path
        self.assertions: list[str] = []
        self.exits: list[Object] = []

    def check(self, name: str, condition: bool, observed: Json, expected: Json) -> None:
        if not condition:
            raise BehavioralError(name, observed, expected)
        self.assertions.append(name)

    def save(self, name: str, data: Object) -> None:
        (self.path / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class Peer:
    """Owns one real child process and a private native data directory."""

    def __init__(self, cluster: Cluster, name: str, state: Path):
        self.cluster = cluster
        self.name = name
        self.state = state
        self.sequence = 0
        self.output: queue.Queue[Object | Exception] = queue.Queue()
        self.stderr: list[str] = []
        self.exchanges: list[Object] = []
        self.exit_recorded = False
        self.process = subprocess.Popen(
            [str(cluster.binary), "--data-dir", str(state), "--name", name, "--network", "loopback"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        cluster.processes.append(self)
        self.readers = [threading.Thread(target=self._stdout, daemon=True), threading.Thread(target=self._stderr, daemon=True)]
        for reader in self.readers:
            reader.start()
        ready = self.receive(60)
        if ready.get("event") != "ready":
            raise SetupError(f"{name} did not emit native readiness")
        identity = string_value(ready.get("device_id"), "ready device_id")
        if len(identity) < 32 or len(identity) > 128:
            raise SetupError("Invalid public endpoint identity")
        self.ready = Ready(identity, self.process.pid)

    def _stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.output.put(parse_object(line, "CLI output"))
        except Exception as error:
            self.output.put(SetupError(f"Malformed CLI output from {self.name}: {type(error).__name__}"))

    def _stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr.append(line.rstrip())

    def receive(self, seconds: float = 60) -> Object:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                item = self.output.get(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                if self.process.poll() is not None:
                    raise SetupError(f"{self.name} exited before its reply (code {self.process.returncode})") from None
                continue
            if isinstance(item, Exception):
                raise item
            return item
        raise SetupError(f"Timed out waiting for {self.name}; timeout is not a mutation kill")

    def request(self, method: str, params: Object | None = None) -> Object:
        self.sequence += 1
        identity = f"{self.name}-{self.process.pid}-{self.sequence}"
        request: Object = {"id": identity, "method": method}
        if params is not None:
            request["params"] = params
        if self.process.poll() is not None or self.process.stdin is None:
            raise SetupError(f"Cannot send to stopped {self.name}")
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        reply = self.receive(90)
        if reply.get("id") != identity or type(reply.get("ok")) is not bool:
            raise SetupError(f"{self.name} reply did not bind request {identity}")
        return reply

    def success(self, method: str, params: Object | None = None) -> Json:
        reply = self.request(method, params)
        if reply["ok"] is not True:
            # Do not include possible ticket contents or private state in public evidence.
            raise SetupError(f"Expected {self.name}.{method} success; native host returned an error")
        return reply.get("result")

    def snapshot(self) -> Object:
        result = object_value(self.success("snapshot"), "snapshot")
        for field in ("workspaces", "resources", "peers"):
            rows(result.get(field), field)
        if result.get("device_id") != self.ready.device_id:
            raise SetupError("Snapshot changed the running endpoint identity")
        if type(result.get("operation_count")) is not int:
            raise SetupError("Snapshot lacks its operation count")
        return result

    def command(self, command: Object) -> Object:
        return object_value(self.success("command", {"command": command}), "command receipt")

    def stop(self) -> None:
        if self.process.poll() is None:
            self.success("shutdown")
        try:
            code = self.process.wait(timeout=35)
        except subprocess.TimeoutExpired as error:
            raise SetupError(f"{self.name} failed to shut down") from error
        self.record_exit(code)
        if code != 0:
            raise SetupError(f"{self.name} shutdown exited {code}")

    def record_exit(self, code: int) -> None:
        if self.exit_recorded:
            return
        self.exit_recorded = True
        for reader in self.readers:
            reader.join(timeout=1)
        self.cluster.evidence.exits.append({"peer": self.name, "pid": self.process.pid, "returncode": code})


class Cluster:
    def __init__(self, binary: Path, evidence: Evidence):
        self.binary = binary
        self.evidence = evidence
        self.private = Path(tempfile.mkdtemp(prefix="workspace-host-acceptance-"))
        self.processes: list[Peer] = []

    def peer(self, name: str) -> Peer:
        return Peer(self, name, self.private / name)

    def cleanup(self) -> None:
        for peer in reversed(self.processes):
            if peer.process.poll() is None:
                try:
                    peer.stop()
                except Exception:
                    peer.process.terminate()
                    try:
                        peer.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        peer.process.kill()
                        peer.process.wait(timeout=5)
            peer.record_exit(peer.process.returncode)
        # Keys, tickets and diagnostic stderr stay outside the exported evidence.
        for index, peer in enumerate(self.processes):
            (self.private / f"{index}-{peer.name}.stderr.log").write_text("\n".join(peer.stderr), encoding="utf-8")
            (self.private / f"{index}-{peer.name}.exchanges.json").write_text(
                json.dumps(peer.exchanges, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )


def workspace(snapshot: Object, identity: str) -> Object:
    matches = [row for row in rows(snapshot["workspaces"], "workspaces") if row.get("id") == identity]
    if len(matches) != 1:
        raise BehavioralError(f"one-workspace-{identity}", len(matches), 1)
    return matches[0]


def resource(snapshot: Object, identity: str) -> Object:
    matches = [row for row in rows(snapshot["resources"], "resources") if row.get("id") == identity]
    if len(matches) != 1:
        raise BehavioralError(f"one-resource-{identity}", len(matches), 1)
    return matches[0]


def head_ids(snapshot: Object, identity: str) -> list[Json]:
    values = sorted(string_value(row.get("id"), "revision id") for row in rows(resource(snapshot, identity)["heads"], "heads"))
    return [*values]


def head_bodies(snapshot: Object, identity: str) -> list[Json]:
    values = sorted(string_value(row.get("body"), "revision body") for row in rows(resource(snapshot, identity)["heads"], "heads"))
    return [*values]


def create_workspace(peer: Peer, identity: str, title: str) -> Object:
    return peer.command({"type": "create_workspace", "operation_id": f"create-{identity}", "workspace_id": identity, "title": title})


def create_note(peer: Peer, identity: str, body: str, workspace_id: str = "launch") -> Object:
    return peer.command(
        {
            "type": "create_resource",
            "operation_id": f"create-{identity}",
            "workspace_id": workspace_id,
            "resource_id": identity,
            "title": identity.title(),
            "body": body,
        }
    )


def edit_note(peer: Peer, operation: str, expected: list[Json], body: str) -> Object:
    return peer.command(
        {
            "type": "edit_resource",
            "operation_id": operation,
            "workspace_id": "launch",
            "resource_id": "brief",
            "expected_heads": expected,
            "title": "Brief",
            "body": body,
        }
    )


def sync(*peers: Peer, allowed_unreachable: set[str] | None = None) -> None:
    for peer in peers:
        for _ in range(3):
            report = object_value(peer.success("sync"), "sync report")
            peer.exchanges.append(report)
            discovered = False
            for status in rows(report.get("peers"), "sync peers"):
                state = object_value(status.get("state"), "sync peer state")
                kind = string_value(state.get("type"), "peer state kind")
                if kind == "synced" or (kind == "unreachable" and status.get("id") in (allowed_unreachable or set())):
                    continue
                if kind == "never_synced":
                    # A completed exchange can introduce another member contact.
                    # That contact was not in the host's initial exchange set.
                    discovered = True
                    continue
                raise SetupError(f"{peer.name} did not complete its peer exchange ({kind}); transport failure is not a mutation kill")
            if not discovered:
                break
        else:
            raise SetupError(f"{peer.name} did not finish exchanging with discovered peers in three passes")


def selected_pairing_and_equal_peers(cluster: Cluster) -> Object:
    check = cluster.evidence.check
    a, b, c = cluster.peer("Laptop"), cluster.peer("Phone"), cluster.peer("Desktop")
    check(
        "independent-device-identities",
        len({a.ready.device_id, b.ready.device_id, c.ready.device_id}) == 3,
        [a.ready.device_id, b.ready.device_id, c.ready.device_id],
        "three distinct public identities",
    )
    create_workspace(a, "launch", "Launch review")
    create_workspace(a, "private", "Private responsibility")
    create_note(a, "brief", "Start with three teams.")
    create_note(a, "private-note", "This note stays on its authorized device.", "private")
    invite = object_value(a.success("invite", {"workspace_ids": ["launch"], "ttl_seconds": 600}), "invite")
    ticket = string_value(invite.get("ticket"), "ticket")
    b.success("join", {"ticket": ticket})
    after_join = b.snapshot()
    ids = sorted(string_value(row.get("id"), "workspace id") for row in rows(after_join["workspaces"], "workspaces"))
    check("pairing-transfers-only-selected-workspace", ids == ["launch"], [*ids], ["launch"])
    check(
        "pairing-transfers-selected-note",
        head_bodies(after_join, "brief") == ["Start with three teams."],
        head_bodies(after_join, "brief"),
        ["Start with three teams."],
    )
    check(
        "pairing-does-not-transfer-private-resource",
        all(row.get("id") != "private-note" for row in rows(after_join["resources"], "resources")),
        [row.get("id") for row in rows(after_join["resources"], "resources")],
        "private-note absent",
    )
    consumed = c.request("join", {"ticket": ticket})
    check("consumed-invite-denies-a-different-peer", consumed["ok"] is False, consumed["ok"], False)
    check("denied-peer-has-no-workspace", c.snapshot()["workspaces"] == [], c.snapshot()["workspaces"], [])
    create_note(b, "phone-note", "Created on the phone peer.")
    sync(b, a)
    check(
        "second-peer-contributes-to-same-workspace",
        head_bodies(a.snapshot(), "phone-note") == ["Created on the phone peer."],
        head_bodies(a.snapshot(), "phone-note"),
        ["Created on the phone peer."],
    )
    first_heads = head_ids(a.snapshot(), "brief")
    edit_note(a, "laptop-first-edit", first_heads, "Decision made on laptop.")
    sync(a, b)
    check(
        "first-peer-edit-reaches-second",
        head_bodies(b.snapshot(), "brief") == ["Decision made on laptop."],
        head_bodies(b.snapshot(), "brief"),
        ["Decision made on laptop."],
    )
    return {
        "a": a.ready.device_id,
        "b": b.ready.device_id,
        "c": c.ready.device_id,
        "selected_workspace": "launch",
        "independent_processes": True,
        "transport": "Iroh loopback",
    }


def offline_recovery_and_replay(cluster: Cluster) -> Object:
    check = cluster.evidence.check
    a, b = cluster.peer("EditingLaptop"), cluster.peer("EditingPhone")
    create_workspace(a, "launch", "Launch review")
    create_note(a, "brief", "Shared starting point.")
    invite = object_value(a.success("invite", {"workspace_ids": ["launch"], "ttl_seconds": 600}), "invite")
    b.success("join", {"ticket": string_value(invite.get("ticket"), "ticket")})
    sync(b, a)
    base = head_ids(a.snapshot(), "brief")
    b.success("foreground", {"foreground": False})
    check("paused-peer-network-is-explicit", b.snapshot()["network"] == {"type": "paused"}, b.snapshot()["network"], {"type": "paused"})
    edit_note(a, "laptop-offline-branch", base, "Laptop wording.")
    edit_note(b, "phone-offline-branch", base, "Phone wording.")
    check(
        "offline-local-edit-is-retained",
        head_bodies(b.snapshot(), "brief") == ["Phone wording."],
        head_bodies(b.snapshot(), "brief"),
        ["Phone wording."],
    )
    b.success("foreground", {"foreground": True})
    sync(b, a)
    expected_bodies = ["Laptop wording.", "Phone wording."]
    check(
        "reconnect-preserves-both-concurrent-bodies",
        head_bodies(a.snapshot(), "brief") == expected_bodies,
        head_bodies(a.snapshot(), "brief"),
        [*expected_bodies],
    )
    check(
        "both-peers-observe-same-conflict-heads",
        head_ids(a.snapshot(), "brief") == head_ids(b.snapshot(), "brief"),
        head_ids(b.snapshot(), "brief"),
        head_ids(a.snapshot(), "brief"),
    )
    heads = head_ids(b.snapshot(), "brief")
    resolve: Object = {
        "type": "edit_resource",
        "operation_id": "reviewed-resolution",
        "workspace_id": "launch",
        "resource_id": "brief",
        "expected_heads": heads,
        "title": "Brief",
        "body": "Both choices reviewed.",
    }
    receipt = b.command(resolve)
    check("explicit-resolution-is-applied", receipt.get("type") == "applied", receipt.get("type"), "applied")
    sync(b, a)
    check(
        "resolved-revision-reaches-other-peer",
        head_bodies(a.snapshot(), "brief") == ["Both choices reviewed."],
        head_bodies(a.snapshot(), "brief"),
        ["Both choices reviewed."],
    )
    historical = sorted(string_value(row.get("body"), "history body") for row in rows(resource(b.snapshot(), "brief")["history"], "history"))
    check(
        "resolution-retains-earlier-writing",
        historical == ["Both choices reviewed.", "Laptop wording.", "Phone wording.", "Shared starting point."],
        [*historical],
        ["Both choices reviewed.", "Laptop wording.", "Phone wording.", "Shared starting point."],
    )
    before = b.snapshot()["operation_count"]
    stale = edit_note(b, "stale-edit", base, "This stale edit must not become current.")
    check("stale-edit-reports-conflict", stale.get("type") == "conflict", stale.get("type"), "conflict")
    check("stale-edit-adds-no-operation", b.snapshot()["operation_count"] == before, b.snapshot()["operation_count"], before)
    replay = b.command(resolve)
    check(
        "exact-command-replay-is-identified",
        replay.get("type") == "applied" and replay.get("replayed") is True,
        replay,
        {"type": "applied", "operation_id": "reviewed-resolution", "workspace_id": "launch", "replayed": True},
    )
    check("exact-command-replay-adds-no-operation", b.snapshot()["operation_count"] == before, b.snapshot()["operation_count"], before)
    changed = b.command({**resolve, "body": "Reusing the same identity with different content"})
    check("changed-command-identity-is-rejected", changed.get("type") == "rejected", changed.get("type"), "rejected")
    check("changed-replay-adds-no-operation", b.snapshot()["operation_count"] == before, b.snapshot()["operation_count"], before)
    invalid: Object = {
        "type": "create_resource",
        "operation_id": "correctable-note",
        "workspace_id": "launch",
        "resource_id": "corrected",
        "title": "",
        "body": "An initially invalid note.",
    }
    rejected = b.command(invalid)
    check(
        "invalid-command-has-definitive-rejection",
        rejected.get("type") == "rejected" and rejected.get("operation_id") == "correctable-note",
        rejected.get("type"),
        "rejected with original operation identity",
    )
    check("rejected-command-adds-no-operation", b.snapshot()["operation_count"] == before, b.snapshot()["operation_count"], before)
    corrected = b.command({**invalid, "title": "Corrected title"})
    check(
        "corrected-uncommitted-command-can-succeed",
        corrected.get("type") == "applied" and corrected.get("replayed") is False,
        corrected.get("type"),
        "applied",
    )
    sync(b, a)
    return restart_and_third_member(cluster, a, b, resolve)


def restart_and_third_member(cluster: Cluster, a: Peer, b: Peer, resolve: Object) -> Object:
    check = cluster.evidence.check
    prior = b.snapshot()
    b.stop()
    b = cluster.peer("EditingPhone")
    reopened = b.snapshot()
    check("restart-retains-native-device-identity", reopened["device_id"] == prior["device_id"], reopened["device_id"], prior["device_id"])
    check("restart-retains-resource-history", reopened["resources"] == prior["resources"], reopened["resources"], prior["resources"])
    replay = b.command(resolve)
    check(
        "restart-retains-exact-replay-receipt",
        replay.get("type") == "applied" and replay.get("replayed") is True,
        replay,
        {"type": "applied", "operation_id": "reviewed-resolution", "workspace_id": "launch", "replayed": True},
    )
    sync(b, a)
    a_identity = a.ready.device_id
    a.stop()
    c = cluster.peer("ThirdDevice")
    invite = object_value(b.success("invite", {"workspace_ids": ["launch"], "ttl_seconds": 600}), "invite")
    c.success("join", {"ticket": string_value(invite.get("ticket"), "ticket")})
    check(
        "member-can-enroll-with-origin-peer-stopped",
        head_bodies(c.snapshot(), "brief") == ["Both choices reviewed."],
        head_bodies(c.snapshot(), "brief"),
        ["Both choices reviewed."],
    )
    edit_note(c, "third-device-edit", head_ids(c.snapshot(), "brief"), "Contribution while the origin peer is stopped.")
    sync(c, allowed_unreachable={a_identity})
    check(
        "third-device-contributes-without-origin-peer",
        head_bodies(b.snapshot(), "brief") == ["Contribution while the origin peer is stopped."],
        head_bodies(b.snapshot(), "brief"),
        ["Contribution while the origin peer is stopped."],
    )
    a = cluster.peer("EditingLaptop")
    sync(a, b, c)
    check(
        "origin-peer-recovers-after-new-member-work",
        head_bodies(a.snapshot(), "brief") == ["Contribution while the origin peer is stopped."],
        head_bodies(a.snapshot(), "brief"),
        ["Contribution while the origin peer is stopped."],
    )
    final_heads = head_ids(a.snapshot(), "brief")
    check(
        "three-peers-converge-after-restart",
        final_heads == head_ids(b.snapshot(), "brief") == head_ids(c.snapshot(), "brief"),
        [final_heads, head_ids(b.snapshot(), "brief"), head_ids(c.snapshot(), "brief")],
        "same revision identities",
    )
    identities = {a.ready.device_id, b.ready.device_id, c.ready.device_id}
    contacts = [sorted(string_value(row.get("id"), "peer id") for row in rows(peer.snapshot()["peers"], "peers")) for peer in (a, b, c)]
    expected_contacts = [sorted(identities - {peer.ready.device_id}) for peer in (a, b, c)]
    check("three-peers-discover-each-other", contacts == expected_contacts, [[*row] for row in contacts], [[*row] for row in expected_contacts])
    return {
        "offline_versions_preserved": True,
        "origin_peer_was_stopped": True,
        "third_member_contributed": True,
        "restarted_device_ids": [a.ready.device_id, b.ready.device_id],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--evidence", type=Path, required=True)
    options = parser.parse_args(namespace=Options())
    binary, source = options.binary.resolve(), options.source.resolve()
    evidence = Evidence(options.evidence.resolve())
    before = source_hashes(source)
    report: Object = {
        "kind": "native-workspace-process-acceptance-v1",
        "passed": False,
        "binary_sha256": digest(binary),
        "source_hashes": {**before},
        "harness_sha256": digest(Path(__file__)),
        "python": sys.version,
        "scope": "Real independent native host processes and Turso stores over isolated Iroh loopback.",
        "limits": [
            "No physical iPhone installation or native UI result.",
            "No internet/NAT/relay or managed device policy result.",
            "No external agent/application effect.",
        ],
    }
    cluster = Cluster(binary, evidence)
    try:
        result = selected_pairing_and_equal_peers(cluster)
        cases: list[Json] = [{"name": "selected_pairing_and_equal_peers", "passed": True, "result": result}]
        report["cases"] = cases
        result = offline_recovery_and_replay(cluster)
        cases.append({"name": "offline_recovery_and_replay", "passed": True, "result": result})
        report["passed"] = True
    except BehavioralError as error:
        report["failure"] = {"type": "behavioral", "assertion": error.assertion, "observed": error.observed, "expected": error.expected}
    except Exception as error:
        report["failure"] = {"type": "setup", "detail": str(error)}
    finally:
        cluster.cleanup()
        report["assertions"] = [*evidence.assertions]
        report["process_exits"] = [*evidence.exits]
        if report["passed"] is True and any(row["returncode"] != 0 for row in evidence.exits):
            report["passed"] = False
            report["failure"] = {"type": "setup", "detail": "A native process did not exit cleanly"}
        report["source_unchanged"] = before == source_hashes(source)
        if report["source_unchanged"] is not True:
            report["passed"] = False
            report["failure"] = {"type": "setup", "detail": "Source changed during acceptance run"}
        report["binary_unchanged"] = report["binary_sha256"] == digest(binary)
        if report["binary_unchanged"] is not True:
            report["passed"] = False
            report["failure"] = {"type": "setup", "detail": "Executable changed during acceptance run"}
        evidence.save("report.json", report)
    summary: Object = {
        "passed": report["passed"],
        "assertions": len(evidence.assertions),
        "failure": report.get("failure"),
        "report": str(evidence.path / "report.json"),
    }
    print(json.dumps(summary, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
