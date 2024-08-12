"""Module for summarizing GitHub activity and creating a summary discussion post."""

from __future__ import annotations

import logging
import sys
import time
from argparse import SUPPRESS, ArgumentParser, Namespace
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any

import actions

from repo_summary_post.logging_utils import configure_logging
from repo_summary_post.summary_generation import generate_summary

if TYPE_CHECKING:
    from collections.abc import Callable


def get_config(
    args: Namespace,
    input_name: str,
    default: Any = SUPPRESS,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    """Get configuration value with precedence: arg > input > default."""
    # If the command line argument is provided, use it
    arg_value = getattr(args, input_name, SUPPRESS)
    if arg_value != SUPPRESS:
        return arg_value

    # If there's no default value, the input is required
    required = default == SUPPRESS
    if isinstance(default, bool):
        # `get_boolean_input` doesn't handle defaults, do it explicitly
        if not actions.core.get_input(input_name, required=required):
            return default
        return actions.core.get_boolean_input(input_name, required=True)
    if isinstance(default, int):
        input_value = actions.core.get_input(input_name, required=required)
        return int(input_value) if input_value else default
    return actions.core.get_input(input_name, required=required) or default


def write_output(content: str, title: str | None, output_path: str | None) -> None:
    """Write content with title to a file or stdout."""
    full_content = f"{title}\n\n{content}" if title else content
    if output_path is None or output_path == "-":
        sys.stdout.write(full_content)
        sys.stdout.write("\n")
    else:
        try:
            Path(output_path).write_text(full_content, encoding="utf-8")
            actions.core.info(f"Content written to {output_path}")
        except OSError as e:
            actions.core.error(f"Error writing to file {output_path}: {e}")


def measure_time(func: Callable[..., Any]) -> Callable[..., Any]:
    """Measure the execution time of a function."""

    @wraps(func)
    def wrapper(*args: object, **kwargs: object) -> object:
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        duration = end_time - start_time
        logging.info("%s took %.2f seconds", func.__name__, duration)
        return result

    return wrapper


@measure_time
def main() -> None:
    """Summarize PRs and create a discussion if category is provided."""
    parser = ArgumentParser(
        description="Summarize GitHub activity",
        argument_default=SUPPRESS,  # defaults specified in `get_config()` calls below
    )
    parser.add_argument(
        "--output-content",
        help="Path to render the GitHub activity report used as input for the LLM",
    )
    parser.add_argument("--output", help="Path to output the AI summary")
    parser.add_argument(
        "--output-prompt",
        help="Path to output the LLM prompt (use '-' for stdout)",
    )
    parser.add_argument(
        "-m",
        "--model",
        help=(
            "LLM model to use for generating the summary"
            " (can also be set via 'model' input)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        help=(
            "Increase verbosity (use -v for INFO, -vv for DEBUG, "
            "can also be set via 'verbose' input)"
        ),
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help=(
            "Dry run mode: don't post the discussion"
            " (can also be set via 'dry-run' input)"
        ),
    )
    parser.add_argument(
        "--github-token",
        help="GitHub token (can also be set via 'github-token' input)",
    )
    parser.add_argument(
        "--repo-name",
        help=(
            "Repository name in the format 'owner/repo'"
            " (can also be set via 'repo-name' input)"
        ),
    )
    parser.add_argument(
        "--category",
        help="Discussion category (can also be set via 'category' input)",
    )
    parser.add_argument(
        "--project-name",
        help="Name of the project (can also be set via 'project-name' input)",
    )
    parser.add_argument(
        # not supported via GitHub action inputs, only for local debugging
        "--start",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC).date(),
        default=None,
        help="Start date for the summary (format: YYYY-MM-DD)",
    )
    parser.add_argument(
        # not supported via GitHub action inputs, only for local debugging
        "--cache",
        action="store_true",
        default=False,
        help="Enable caching for GraphQL queries",
    )
    args = parser.parse_args()

    configure_logging(getattr(args, "verbose", 0))

    github_token = get_config(args, "github_token")
    repo_owner_and_name = get_config(args, "repo_name")
    project_name = get_config(args, "project_name")
    category = get_config(args, "category", "Announcements")
    model = get_config(args, "model", "openrouter/anthropic/claude-3.5-sonnet:beta")
    verbose = get_config(args, "verbose", 0)
    dry_run = get_config(args, "dry_run", default=False)
    output_content = get_config(args, "output_content", None)
    output = get_config(args, "output", None)
    output_prompt = get_config(args, "output_prompt", None)

    if not github_token:
        actions.core.error(
            "GitHub token is required."
            " Please provide it via --github-token or 'github-token' input.",
        )
        sys.exit(1)

    if not repo_owner_and_name:
        actions.core.error(
            "Repository name is required."
            " Please provide it via --repo-name or 'repo-name' input.",
        )
        sys.exit(1)

    configure_logging(verbose)

    activity_report, title, ai_summary, prompt = generate_summary(
        github_token,
        repo_owner_and_name,
        project_name,
        category,
        model,
        args.start,
        use_cache=args.cache,
        dry_run=dry_run,
    )

    if output_content:
        write_output(activity_report, title=None, output_path=output_content)

    if output or output is None:
        write_output(ai_summary, title=title, output_path=output)

    if output_prompt:
        write_output(prompt, title=None, output_path=output_prompt)


if __name__ == "__main__":
    main()
