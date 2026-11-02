"""Which stored reading the dashboard is working from.

Every page, every report and the agent chain ask this module rather than querying the
readings table themselves. Before it existed each caller passed its own default -
mostly the literal "field-1" - so a reading taken into any other plot was invisible
everywhere except the page that took it.

There is no mode any more. The simulator is gone, so a reading exists because a device
sent it or because the farmer typed one in; both are real data about this farm. What
each number is worth is carried by its provenance, not by a global switch.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ..core import storage


def current(parcel_id: str = "") -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """The reading in force, plus why it is that one.

    ``parcel_id`` empty means "whatever plot the newest reading came from", which is
    what every page wants by default. Passing one narrows to that plot.
    """
    reading = storage.latest_reading(parcel_id or None)
    meta: Dict[str, Any] = {"parcel_id": parcel_id or None, "note": ""}
    if reading:
        return reading, meta
    meta["note"] = ("No reading yet for this field. Connect the HM-10 on the Sensor "
                    "page and press Fetch, or enter your own soil-test figures.")
    return None, meta
