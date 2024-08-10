"""Module for handling caching configuration and setup."""

import hashlib
import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict


def configure_caching_logging():
    """Configure logging for caching-related operations."""
    caching_logger = logging.getLogger("caching")
    caching_logger.setLevel(logging.DEBUG)

    # Custom filter to exclude sensitive information
    class ExcludeSensitiveFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            message = record.getMessage()
            return not any(
                sensitive in message for sensitive in ["token", "key", "password"]
            )

    caching_logger.addFilter(ExcludeSensitiveFilter())


from gql import Client
from gql.transport.requests import RequestsHTTPTransport


def cached_execute(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a GraphQL query with caching.

    Args:
        query (str): The GraphQL query string.
        variables (Dict[str, Any]): The variables for the query.

    Returns:
        Dict[str, Any]: The result of the query execution.
    """
    # Create a unique key for this query and variables
    key = hashlib.md5(
        f"{query}{json.dumps(variables, sort_keys=True)}".encode()
    ).hexdigest()

    @lru_cache(maxsize=100)
    def _cached_execute(key: str) -> Dict[str, Any]:
        # Log cache hit/miss
        cache_info = _cached_execute.cache_info()
        is_hit = cache_info.hits > cache_info.misses
        logging.getLogger("caching").debug(
            f"Cache {'hit' if is_hit else 'miss'} for key: {key}"
        )

        # Execute the query if it's not in the cache
        transport = RequestsHTTPTransport(
            url="https://api.github.com/graphql",
            headers={"Authorization": f'Bearer {os.environ["INPUT_GITHUB_TOKEN"]}'},
        )
        client = Client(transport=transport, fetch_schema_from_transport=True)
        return client.execute(query, variable_values=variables)

    return _cached_execute(key)


# Note: The create_cached_session function is kept for potential future use
def create_cached_session():
    """Placeholder for potential future use of requests-cache."""
    pass
