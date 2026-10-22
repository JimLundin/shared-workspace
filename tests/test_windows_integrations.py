"""Native credential protection and owned process cleanup."""

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import TestCase

if sys.platform != "win32":
    raise unittest.SkipTest("Windows integration primitives")

from assistant.teams import TokenStore
from winapi.processes import ProcessJob
from winapi.protection import protect, unprotect


def test_dpapi_roundtrip_and_tamper_rejection(tmp_path: Path) -> None:
    secret = b"fixture-only-integration-secret"
    encrypted = protect(secret)
    assert secret not in encrypted and unprotect(encrypted) == secret
    with TestCase().assertRaises(OSError):
        unprotect(encrypted[:len(encrypted) // 2])
    store = TokenStore(tmp_path / "tokens.bin")
    store.write({"access_token": secret.decode()})
    assert secret not in store.path.read_bytes() and store.read()["access_token"] == secret.decode()
    store.clear()
    assert not store.path.exists()


def test_job_closure_stops_an_owned_process_and_its_child(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    child_ready = tmp_path / "child-ready"
    gate = tmp_path / "go"
    child = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)"
    parent = (
        "import pathlib,subprocess,sys,time; gate=pathlib.Path(sys.argv[1]); pathlib.Path(sys.argv[2]).touch(); "
        "exec('while not gate.exists(): time.sleep(.01)'); "
        "subprocess.Popen([sys.executable,'-c',sys.argv[3],sys.argv[4]], creationflags=0x08000000); time.sleep(60)"
    )
    job = ProcessJob()
    process = subprocess.Popen([sys.executable, "-c", parent, str(gate), str(ready), child, str(child_ready)], creationflags=0x08000000)
    try:
        job.add(process.pid)
        gate.touch()
        deadline = time.monotonic() + 5
        while not child_ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists() and child_ready.exists()
        job.close()
        process.wait(timeout=3)
        assert process.returncode is not None
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_job_handle_also_closes_when_its_owner_exits(tmp_path: Path) -> None:
    marker = tmp_path / "child-started"
    late = tmp_path / "child-survived"
    child = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(2); pathlib.Path(sys.argv[2]).touch(); time.sleep(60)"
    source = str(Path(__file__).resolve().parent.parent)
    parent = (
        f"import sys; sys.path.insert(0,{json.dumps(source)}); "
        "import subprocess,time,pathlib; from winapi.processes import ProcessJob; job=ProcessJob(); "
        "process=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]], creationflags=0x08000000); "
        "job.add(process.pid); marker=pathlib.Path(sys.argv[2]); deadline=time.monotonic()+5; "
        "exec('while not marker.exists() and time.monotonic()<deadline: time.sleep(.02)'); assert marker.exists()"
    )
    subprocess.run([sys.executable, "-c", parent, child, str(marker), str(late)], check=True, timeout=8, creationflags=0x08000000)
    time.sleep(2.1)
    assert marker.exists() and not late.exists()
