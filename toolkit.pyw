"""Double-click launcher: the keeper, which runs the layout and the assistant."""

from runtime import activate

activate()

from keeper import main  # noqa: E402

main()
