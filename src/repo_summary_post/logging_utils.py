"""Utility functions for logging configuration."""

import logging

import actions.core


class GithubActionsHandler(logging.Handler):
    """Custom logging handler for GitHub Actions.

    This handler emits log messages to GitHub Actions, using the appropriate
    logging level (error, warning, or info).

    """

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to GitHub Actions.

        Args:
        ----
            record: The log record to be emitted.

        """
        log_entry = record.getMessage()
        if record.levelno >= logging.ERROR:
            actions.core.error(log_entry)
        elif record.levelno >= logging.WARNING:
            actions.core.warning(log_entry)
        else:
            actions.core.info(log_entry)


def configure_logging(verbosity: int) -> None:
    """Configure logging based on verbosity level."""
    if verbosity == 0:
        log_level = logging.WARNING
    elif verbosity == 1:
        log_level = logging.INFO
    else:
        log_level = logging.DEBUG

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create and add handler
    github_actions_handler = GithubActionsHandler()
    github_actions_handler.setLevel(log_level)

    root_logger.addHandler(github_actions_handler)

    # Set specific loggers to WARNING level
    logging.getLogger("gql.transport.requests").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)
    logging.getLogger("httpcore.http11").setLevel(logging.WARNING)
    logging.getLogger("httpcore.connection").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
