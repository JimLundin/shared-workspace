"""Double-click launcher: the keeper runs the layout, assistant, and automation."""

from runtime import activate, bootstrap

bootstrap()
activate()

from keeper import main  # noqa: E402

main()
