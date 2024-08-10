"""Module for handling caching configuration and setup."""

import hashlib
import json
import logging
import os
from typing import Any, cast

from diskcache import Cache
from gql import Client
from gql.transport.requests import RequestsHTTPTransport
from graphql import DocumentNode

# Initialize the disk cache
cache = Cache("./cache")


def configure_caching_logging() -> None:
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


def cached_execute(query: DocumentNode, variables: dict[str, Any]) -> dict[str, Any]:
    """Execute a GraphQL query with disk-based caching.

    Args:
    ----
        query (str): The GraphQL query string.
        variables (Dict[str, Any]): The variables for the query.

    Returns:
    -------
        Dict[str, Any]: The result of the query execution.

    """
    # Create a unique key for this query and variables
    key = hashlib.md5(  # noqa: S324
        f"{query}{json.dumps(variables, sort_keys=True)}".encode(),
    ).hexdigest()

    # Check if the result is in the cache
    result = cast(dict[str, Any], cache.get(key))
    if result is not None:
        logging.getLogger("caching").debug("Cache hit for key: %s", key)
        return result

    logging.getLogger("caching").debug("Cache miss for key: %s", key)

    # Execute the query if it's not in the cache
    transport = RequestsHTTPTransport(
        url="https://api.github.com/graphql",
        headers={"Authorization": f'Bearer {os.environ["INPUT_GITHUB_TOKEN"]}'},
    )
    client = Client(transport=transport, fetch_schema_from_transport=True)
    result = client.execute(query, variable_values=variables)

    # Store the result in the cache
    cache.set(key, result)

    return result


def clear_cache() -> None:
    """Clear the entire cache."""
    cache.clear()


def get_cache_info() -> dict[str, int]:
    """Get information about the current state of the cache."""
    return {
        "size": cache.volume(),
        "item_count": len(cache),
    }
