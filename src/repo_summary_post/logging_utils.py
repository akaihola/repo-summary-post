"""Utility functions for logging configuration."""

import logging

import actions.core


class GithubActionsHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
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

    # Create and add handlers
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    github_actions_handler = GithubActionsHandler()
    github_actions_handler.setLevel(log_level)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    github_actions_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(github_actions_handler)

    # Set specific loggers to WARNING level
    logging.getLogger("gql.transport.requests").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)
