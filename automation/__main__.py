"""Start the supervised work process with the pinned runtime."""

import os
import sys

from runtime import activate


def main() -> None:
    if sys.platform != "win32":
        sys.exit("the work process only runs on Windows")
    activate()
    from assistant.llm import settings_from  # noqa: PLC0415

    from .app import report, run  # noqa: PLC0415

    try:
        run(settings_from(os.environ))
    except Exception as error:
        report(f"Work process stopped: {type(error).__name__}.")
        print(f"Work process stopped ({type(error).__name__}); see toolkit/work/worker.log and the work overview.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
