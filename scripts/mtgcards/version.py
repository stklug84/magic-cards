"""Distribution version of the packaged simulator tooling.

Single source of truth for the ``magic-cards-tools`` version: resolved
from the installed distribution metadata rather than restated in each
package, so a source checkout and an installed wheel can never disagree.

Consumers (e.g. a deck repository pinning a release) use this together
with :mod:`mtgcards.graph` to report a precise tools/graph pairing when a
graph bundle turns out to be incompatible.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

#: name of the distribution these packages are published as
DISTRIBUTION = "magic-cards-tools"

#: reported when the packages are imported from a source checkout that
#: was never installed (CI runs the test suites straight from scripts/)
UNKNOWN_VERSION = "0+source"


def tools_version() -> str:
    """Return the installed ``magic-cards-tools`` version.

    Falls back to :data:`UNKNOWN_VERSION` when the packages are imported
    from an uninstalled source tree.
    """
    try:
        return version(DISTRIBUTION)
    except PackageNotFoundError:
        return UNKNOWN_VERSION


__version__ = tools_version()
