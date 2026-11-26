from abc import ABC, abstractmethod

from tracker.models import Snapshot


class TrackingError(Exception):
    """An expected provider error, with a message safe to show to users."""


class NotConfigured(TrackingError):
    """The user has not configured a provider endpoint yet."""


class RateLimited(TrackingError):
    def __init__(self, seconds: float = 60) -> None:
        super().__init__("Hãng đang giới hạn hoặc yêu cầu xác minh truy cập; bot sẽ thử lại sau.")
        self.seconds = max(1, seconds)


class CarrierAdapter(ABC):
    code: str
    name: str

    @property
    def configured(self) -> bool:
        return True

    @abstractmethod
    async def track(self, tracking_number: str) -> Snapshot:
        """Return all available events, with oldest first after normalization."""
