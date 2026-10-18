"""The keeper: runs the toolkit, installs changes, and undoes the ones that fail.
Its logic runs on fake processes and a fake clock; its file handling on real folders."""

from __future__ import annotations

from pathlib import Path

from keeper import PROTECTED, Keeper, Stage, ask, install, restore, snapshot, source_files, take_requests

# ---- a fake world for the keeper's logic -----------------------------------------------


class FakeChild:
    __slots__ = ("code", "stopped")

    def __init__(self) -> None:
        self.code: int | None = None
        self.stopped = False

    def poll(self) -> int | None:
        return self.code

    def stop(self) -> None:
        self.stopped = True
        self.code = 0


class FakeCheck:
    __slots__ = ("outcome",)

    def __init__(self) -> None:
        self.outcome: tuple[bool, str] | None = None

    def result(self) -> tuple[bool, str] | None:
        return self.outcome


class World:
    """What the keeper did, and knobs for what happens to it."""

    __slots__ = ("check", "children", "log", "ready", "versions")

    def __init__(self) -> None:
        self.log: list[str] = []
        self.children: dict[str, FakeChild] = {}
        self.ready: set[str] = set()
        self.check = FakeCheck()
        self.versions = 0

    def start(self, name: str) -> FakeChild:
        self.log.append(f"start {name}")
        self.ready.discard(name)
        child = FakeChild()
        self.children[name] = child
        return child

    def is_ready(self, name: str) -> bool:
        return name in self.ready

    def start_check(self) -> FakeCheck:
        self.log.append("check")
        self.check = FakeCheck()
        return self.check

    def snapshot(self) -> str:
        self.versions += 1
        self.log.append(f"snapshot v{self.versions}")
        return f"v{self.versions}"

    def restore(self, version: str) -> None:
        self.log.append(f"restore {version}")

    def install(self) -> None:
        self.log.append("install")

    def notify(self, text: str) -> None:
        self.log.append(f"notify {text.splitlines()[0]}")

    def show_trial(self, summary: str | None) -> None:
        self.log.append(f"trial {summary}" if summary else "trial over")

    def come_up(self) -> None:
        self.ready |= {"layout", "assistant"}

    def take(self) -> list[str]:
        log, self.log = self.log, []
        return log


def running(world: World) -> Keeper:
    """A keeper with both programs up."""
    keeper = Keeper(world)
    keeper.start(now=0)
    world.come_up()
    keeper.tick(now=1)
    world.take()
    return keeper


def installed(world: World, keeper: Keeper) -> None:
    """An install that passes its checks and comes up: now on trial."""
    keeper.request("install", "a new snippet", now=10)
    world.check.outcome = (True, "ok")
    keeper.tick(now=20)
    world.come_up()
    keeper.tick(now=21)


# ---- starting and running --------------------------------------------------------------


def test_starts_both_programs() -> None:
    world = World()
    keeper = Keeper(world)
    keeper.start(now=0)
    world.come_up()
    keeper.tick(now=1)
    assert world.take() == ["start layout", "start assistant"]
    assert keeper.stage is Stage.RUNNING


def test_a_crash_is_restarted() -> None:
    world = World()
    keeper = running(world)
    world.children["layout"].code = 1
    keeper.tick(now=5)
    assert world.take() == ["start layout"]


def test_quitting_the_layout_on_purpose_leaves_it_stopped_until_asked() -> None:
    world = World()
    keeper = running(world)
    world.children["layout"].code = 0  # the escape key held
    keeper.tick(now=5)
    assert world.take() == []
    keeper.request("restart_layout", "", now=6)
    assert world.take() == ["start layout"]


def test_quitting_the_assistant_quits_everything() -> None:
    world = World()
    keeper = running(world)
    world.children["assistant"].code = 0  # Quit in the tray menu
    keeper.tick(now=5)
    assert world.children["layout"].stopped
    assert keeper.done


def test_crashing_over_and_over_without_an_update_gives_up_with_a_notice() -> None:
    world = World()
    keeper = running(world)
    for t in range(4):
        world.children["layout"].code = 1
        keeper.tick(now=10 + t)
    assert world.take()[-1] == "notify The layout keeps crashing, so it's stopped. See layout.log."


# ---- installing ------------------------------------------------------------------------


def test_an_install_is_checked_snapshotted_installed_and_tried() -> None:
    world = World()
    keeper = running(world)
    installed(world, keeper)
    assert world.take() == ["check", "snapshot v1", "install", "start layout", "start assistant", "trial a new snippet"]
    assert keeper.stage is Stage.TRIAL


def test_a_change_that_fails_its_checks_is_not_installed() -> None:
    world = World()
    keeper = running(world)
    keeper.request("install", "broken", now=10)
    world.check.outcome = (False, "FAILED tests.test_engine.test_plain_letters")
    keeper.tick(now=20)
    assert world.take() == ["check", "notify Not installed: the change failed its checks."]
    assert not world.children["layout"].stopped
    assert keeper.stage is Stage.RUNNING


def test_a_change_that_doesnt_come_up_is_undone() -> None:
    world = World()
    keeper = running(world)
    keeper.request("install", "slow", now=10)
    world.check.outcome = (True, "ok")
    keeper.tick(now=20)
    world.take()
    keeper.tick(now=20 + 31)  # never ready
    assert world.take() == ["restore v1", "start layout", "start assistant", "notify Update undone: it didn't start."]


def test_a_change_that_exits_while_starting_is_undone_at_once() -> None:
    world = World()
    keeper = running(world)
    keeper.request("install", "crashy", now=10)
    world.check.outcome = (True, "ok")
    keeper.tick(now=20)
    world.take()
    world.children["assistant"].code = 1
    keeper.tick(now=21)
    assert world.take()[0] == "restore v1"


def test_a_crash_during_the_trial_undoes_the_change() -> None:
    world = World()
    keeper = running(world)
    installed(world, keeper)
    world.take()
    world.children["layout"].code = 1
    keeper.tick(now=100)
    assert world.take() == ["restore v1", "start layout", "start assistant", "notify Update undone: it crashed during its trial.", "trial over"]


def test_quitting_the_layout_during_the_trial_isnt_a_crash() -> None:
    world = World()
    keeper = running(world)
    installed(world, keeper)
    world.take()
    world.children["layout"].code = 0  # the escape key held
    keeper.tick(now=100)
    assert world.take() == []
    assert keeper.stage is Stage.TRIAL


def test_the_trial_ends_after_ten_minutes_and_the_change_stays() -> None:
    world = World()
    keeper = running(world)
    installed(world, keeper)
    world.take()
    keeper.tick(now=21 + 601)
    assert world.take() == ["trial over"]
    assert keeper.stage is Stage.RUNNING


def test_keep_ends_the_trial_and_undo_rolls_back() -> None:
    world = World()
    keeper = running(world)
    installed(world, keeper)
    keeper.request("keep", "", now=30)
    assert keeper.stage is Stage.RUNNING
    world.take()
    keeper.request("undo", "", now=40)  # undo works after the trial too
    assert world.take()[0] == "restore v1"


def test_crashing_over_and_over_after_an_update_undoes_it() -> None:
    world = World()
    keeper = running(world)
    installed(world, keeper)
    keeper.request("keep", "", now=30)
    world.take()
    for t in range(4):
        world.children["assistant"].code = 1
        keeper.tick(now=40 + t)
    assert "restore v1" in world.take()


def test_one_thing_at_a_time() -> None:
    world = World()
    keeper = running(world)
    keeper.request("install", "first", now=10)
    keeper.request("install", "second", now=11)
    assert world.take() == ["check", "notify Busy installing; try again in a moment."]


# ---- files -----------------------------------------------------------------------------


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def test_source_files_leave_out_caches_and_history(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": "", "pkg/b.py": "", "pkg/__pycache__/b.pyc": "", ".git/HEAD": "", "README.md": "", ".mypy_cache/x": ""})
    assert source_files(tmp_path) == ["README.md", "a.py", "pkg/b.py"]


def test_snapshot_restore_and_install(tmp_path: Path) -> None:
    live, versions, workspace = tmp_path / "live", tmp_path / "versions", tmp_path / "workspace"
    write(live, {"config.py": "OLD", "gone.py": "x", "keeper.py": "KEEPER"})
    version = snapshot(live, versions)
    write(workspace, {"config.py": "NEW", "added.py": "y", "keeper.py": "HACKED"})
    install(workspace, live)
    assert (live / "config.py").read_text() == "NEW"
    assert (live / "added.py").exists()
    assert not (live / "gone.py").exists()
    assert (live / "keeper.py").read_text() == "KEEPER"  # protected: never replaced
    restore(versions / version, live)
    assert (live / "config.py").read_text() == "OLD"
    assert (live / "gone.py").exists()
    assert not (live / "added.py").exists()


def test_snapshots_keep_the_last_twenty(tmp_path: Path) -> None:
    live, versions = tmp_path / "live", tmp_path / "versions"
    write(live, {"a.py": ""})
    for _ in range(25):
        snapshot(live, versions)
    assert len(list(versions.iterdir())) == 20


def test_requests_arrive_in_order(tmp_path: Path) -> None:
    ask("install", "first", folder=tmp_path)
    ask("undo", "", folder=tmp_path)
    assert take_requests(folder=tmp_path) == [("install", "first"), ("undo", "")]
    assert take_requests(folder=tmp_path) == []


def test_the_keeper_and_its_checker_are_protected() -> None:
    assert {"keeper.py", "selfcheck.py", "toolkit.pyw"} <= PROTECTED
