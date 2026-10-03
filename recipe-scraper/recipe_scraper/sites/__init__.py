from __future__ import annotations

from .base import Site
from .fiber import Fiber
from .gemrielia import Gemrielia
from .generic import GenericSite
from .kerdzebi import Kerdzebi
from .kulinaria import Kulinaria
from .samzareulo import Samzareulo

SITES: dict[str, Site] = {s.name: s for s in (Kulinaria(), Gemrielia(), Fiber(), Samzareulo(), Kerdzebi())}
# receptebi.ge did not resolve when this was written; kept as a generic site in case it returns.
SITES["receptebi"] = GenericSite("receptebi", "https://receptebi.ge")
DEFAULT_SITES = ["kulinaria", "gemrielia", "fiber", "samzareulo", "kerdzebi"]


def get_site(spec: str) -> Site:
    """'kulinaria'  or  'name=https://site.ge'  or  'name=https://site.ge|<url regex>'."""
    if "=" in spec:
        name, rest = spec.split("=", 1)
        base, _, pattern = rest.partition("|")
        return GenericSite(name.strip(), base.strip(), pattern.strip() or None)
    try:
        return SITES[spec]
    except KeyError:
        raise SystemExit(f"unknown site {spec!r}; known: {', '.join(SITES)} (or name=https://url)") from None


__all__ = ["SITES", "DEFAULT_SITES", "Site", "get_site"]
