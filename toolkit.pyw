"""Double-click launcher: the keeper, which runs the layout and the assistant."""

from runtime import activate, bootstrap

bootstrap()
activate()

from keeper import main  # noqa: E402

main()
