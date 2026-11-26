from tracker.adapters.base import CarrierAdapter
from tracker.adapters.configurable import load_profiles
from tracker.adapters.ghn import GHNAdapter
from tracker.adapters.ghtk import GHTKAdapter
from tracker.adapters.spx import SPXAdapter
from tracker.adapters.viettelpost import ViettelPostAdapter
from tracker.config import Settings
from tracker.http import TrackingHTTP


def build_registry(settings: Settings, http: TrackingHTTP) -> dict[str, CarrierAdapter]:
    profiles = load_profiles(settings.carrier_config)
    adapters = [
        SPXAdapter(http, settings.spx_url, settings.timezone),
        GHNAdapter(http, profiles.get("ghn", {}), settings.timezone),
        GHTKAdapter(http, profiles.get("ghtk", {}), settings.timezone),
        ViettelPostAdapter(http, profiles.get("vtp", {}), settings.timezone),
    ]
    return {adapter.code: adapter for adapter in adapters}


def detect_carrier(tracking_number: str) -> str | None:
    code = tracking_number.upper()
    if code.startswith("SPXVN"):
        return "spx"
    if code.startswith(("GY", "G8")):
        return "ghn"
    # Numeric and other ambiguous codes need an explicit user selection.
    return None
