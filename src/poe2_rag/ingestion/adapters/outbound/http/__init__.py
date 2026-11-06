"""Outbound HTTP adapter: a polite, caching fetcher for the hosts this project may fetch."""

from poe2_rag.ingestion.adapters.outbound.http.polite_http_fetcher import (
    POE2DB_POLICY,
    HttpCacheError,
    HttpFetchError,
    HttpRequestError,
    HttpSourcePolicy,
    HttpStatusError,
    HttpTimeoutError,
    MissingHttpContactError,
    PathRule,
    PoliteHttpFetcher,
    RobotsDisallowedError,
    RobotsUnavailableError,
    UnsupportedLocatorError,
)

__all__ = [
    "POE2DB_POLICY",
    "HttpCacheError",
    "HttpFetchError",
    "HttpRequestError",
    "HttpSourcePolicy",
    "HttpStatusError",
    "HttpTimeoutError",
    "MissingHttpContactError",
    "PathRule",
    "PoliteHttpFetcher",
    "RobotsDisallowedError",
    "RobotsUnavailableError",
    "UnsupportedLocatorError",
]
