"""Module for handling caching configuration and setup."""

import logging
from datetime import timedelta
from typing import Any

from requests_cache import CachedSession
from requests_cache.session import CachedSession

def configure_caching_logging():
    """Configure logging for caching-related operations."""
    requests_cache_logger = logging.getLogger("requests_cache")
    requests_cache_logger.setLevel(logging.DEBUG)
    requests_logger = logging.getLogger("requests")
    requests_logger.setLevel(logging.DEBUG)

    # Custom filter to exclude response content
    class ExcludeResponseFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            message = record.getMessage()
            return not message.strip().startswith("<<<")

    requests_cache_logger.addFilter(ExcludeResponseFilter())
    requests_logger.addFilter(ExcludeResponseFilter())

    # Enable request logging
    urllib3_logger = logging.getLogger("urllib3")
    urllib3_logger.setLevel(logging.DEBUG)
    urllib3_logger.propagate = True
    urllib3_logger.addFilter(ExcludeResponseFilter())

def create_cached_session() -> CachedSession:
    """Create and configure a CachedSession for GitHub API requests."""
    cached_session = CachedSession(
        "github_cache",
        backend="sqlite",
        expire_after=timedelta(hours=1),
        allowable_methods=("GET", "POST"),
        cache_control=True,
        stale_if_error=True,
    )

    # Configure POST caching
    if hasattr(cached_session.cache, "urls_expire_after"):
        cached_session.cache.urls_expire_after = {
            "https://api.github.com/graphql": timedelta(hours=1),
        }

    # Add custom cache key for POST requests
    if hasattr(cached_session.cache, "create_key"):
        def create_key(request: Any) -> str:
            return f"{request.method}:{request.url}:{request.body}"
        cached_session.cache.create_key = create_key

    logging.getLogger("requests_cache").debug("CachedSession created")
    return cached_session
