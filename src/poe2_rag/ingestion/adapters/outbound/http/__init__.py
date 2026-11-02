"""Outbound HTTP adapter: a polite, caching fetcher for poe2db."""

from poe2_rag.ingestion.adapters.outbound.http.polite_http_fetcher import (
    HttpFetchError,
    HttpRequestError,
    HttpStatusError,
    HttpTimeoutError,
    MissingHttpContactError,
    Poe2dbCacheError,
    PoliteHttpFetcher,
    RobotsDisallowedError,
    RobotsUnavailableError,
    UnsupportedLocatorError,
)

__all__ = [
    "HttpFetchError",
    "HttpRequestError",
    "HttpStatusError",
    "HttpTimeoutError",
    "MissingHttpContactError",
    "Poe2dbCacheError",
    "PoliteHttpFetcher",
    "RobotsDisallowedError",
    "RobotsUnavailableError",
    "UnsupportedLocatorError",
]
