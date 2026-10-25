"""Restore Explorer's taskbar if the assistant exits while owning its visibility."""

import sys


def main() -> None:
    from winapi.desktop_shell import taskbar_visible, wait_for_exit  # noqa: PLC0415

    pid, *handles = (int(value) for value in sys.argv[1:])
    wait_for_exit(pid, lambda: print("ready", flush=True))
    for handle in handles:
        taskbar_visible(handle, show=True)


if __name__ == "__main__":
    main()
