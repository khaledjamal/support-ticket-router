"""Build an SDK client for Jev, a self-hosted Laya server, or the offline fake.

All three speak the same `POST /v1/systemone` protocol, so the rest of the app uses one client type.
"""

from enum import StrEnum

import httpx2
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from triage.decisions import fake


class Backend(StrEnum):
    FAKE = "fake"
    JEV = "jev"
    LAYA = "laya"


def build_client(
    backend: Backend,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> AsyncTypeSafeClient:
    match backend:
        case Backend.FAKE:
            # The real SDK client with an in-memory transport, so the fake exercises real request and response parsing.
            return AsyncTypeSafeClient(
                api_key="fake",
                base_url="http://fake.invalid",
                transport=httpx2.MockTransport(fake.handle),
                retry=RetryPolicy(max_retries=0),
            )
        case Backend.JEV:
            # A missing key falls back to the SDK's TYPESAFE_API_KEY environment variable.
            return AsyncTypeSafeClient(api_key=api_key, base_url=base_url, model=model, timeout=timeout)
        case Backend.LAYA:
            if not base_url:
                raise ValueError("the laya backend needs a base URL (TRIAGE_LAYA_BASE_URL)")
            # The SDK insists on a key; a Laya server without LAYA_API_KEY ignores it.
            return AsyncTypeSafeClient(
                api_key=api_key or "laya-local", base_url=base_url, model=model, timeout=timeout
            )
