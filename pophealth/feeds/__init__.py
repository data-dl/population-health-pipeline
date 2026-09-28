"""Feed-specific staging and curation. Everything else about a feed comes from its contract."""

from __future__ import annotations

from . import appointments, labs, pharmacy, roster, screenings
from .base import META, FeedSpec

_BUILDERS = {
    "roster": roster.build,
    "screenings": screenings.build,
    "labs": labs.build,
    "appointments": appointments.build,
    "pharmacy": pharmacy.build,
}


def feed_spec(settings, feed: str) -> FeedSpec:
    return _BUILDERS[feed](settings)


__all__ = ["META", "FeedSpec", "feed_spec"]
