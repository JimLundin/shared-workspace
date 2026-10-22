"""Double-click launcher: runs the `miryoku` package with the bundled Python."""

from runtime import bootstrap

bootstrap()

from miryoku.__main__ import main  # noqa: E402

main()
