"""Module for summarizing GitHub Pull Requests and creating a discussion."""

from __future__ import annotations

import argparse
import importlib.resources
import logging
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from collections.abc import Callable

import actions.core  # alternative: https://pypi.org/project/actions-toolkit/
import llm  # type: ignore[import-untyped]
from github import Github
from jinja2 import BaseLoader, Environment, Template
from llm import get_key

from repo_summary_post import __version__
from repo_summary_post.caching import configure_caching_logging
from repo_summary_post.github_utils import create_discussion, summarize_prs


def get_env_or_arg(env_name: str, arg_value: Optional[str]) -> Optional[str]:
    """Get value from environment variable or command-line argument."""
    return arg_value if arg_value is not None else os.environ.get(env_name)


def configure_logging(verbosity: int) -> None:
    """Configure logging based on verbosity level."""
    if verbosity == 0:
        logging.basicConfig(level=logging.WARNING)
    elif verbosity == 1:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.DEBUG)

    logging.getLogger("gql.transport.requests").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)


def write_output(content: str, output_path: str | None) -> None:
    """Write content to a file or stdout."""
    if output_path is None or output_path == "-":
        sys.stdout.write(content)
        sys.stdout.write("\n")
    else:
        try:
            from pathlib import Path

            Path(output_path).write_text(content, encoding="utf-8")
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


def configure_logging(verbosity: int) -> None:
    """Configure logging based on verbosity level."""
    if verbosity == 0:
        logging.basicConfig(level=logging.WARNING)
    elif verbosity == 1:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.DEBUG)

    logging.getLogger("gql.transport.requests").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)


def main() -> None:
    """Summarize PRs and create a discussion if category is provided."""
    parser = argparse.ArgumentParser(description="Summarize GitHub Pull Requests")
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Enable caching for GraphQL queries",
    )
    parser.add_argument(
        "--output-content",
        help="Path to output the rendered GitHub data",
    )
    parser.add_argument("--output", help="Path to output the AI summary")
    parser.add_argument(
        "-m",
        "--model",
        default="openrouter/anthropic/claude-3.5-sonnet:beta",
        help="LLM model to use for generating the summary (can also be set via INPUT_MODEL env var)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (use -v for INFO, -vv for DEBUG, can also be set via INPUT_VERBOSE env var)",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Dry run mode: don't post the discussion (can also be set via INPUT_DRY_RUN env var)",
    )
    parser.add_argument(
        "--github-token",
        help="GitHub token (can also be set via INPUT_GITHUB_TOKEN env var)",
    )
    parser.add_argument(
        "--repo-name",
        help="Repository name in the format 'owner/repo' (can also be set via INPUT_REPO_NAME env var)",
    )
    parser.add_argument(
        "--category",
        help="Discussion category (can also be set via INPUT_CATEGORY env var)",
    )
    args = parser.parse_args()

    configure_logging(args.verbose)

    github_token = get_env_or_arg("INPUT_GITHUB_TOKEN", args.github_token)
    repo_owner_and_name = get_env_or_arg("INPUT_REPO_NAME", args.repo_name)
    category = get_env_or_arg("INPUT_CATEGORY", args.category)
    model = get_env_or_arg("INPUT_MODEL", args.model)
    verbose = int(get_env_or_arg("INPUT_VERBOSE", str(args.verbose)) or "0")
    dry_run = get_env_or_arg("INPUT_DRY_RUN", str(args.dry_run)).lower() in (
        "true",
        "1",
        "yes",
    )

    if not github_token:
        actions.core.error(
            "GitHub token is required. Please provide it via --github-token or INPUT_GITHUB_TOKEN env var."
        )
        sys.exit(1)

    if not repo_owner_and_name:
        actions.core.error(
            "Repository name is required. Please provide it via --repo-name or INPUT_REPO_NAME env var."
        )
        sys.exit(1)

    if args.cache:
        configure_caching_logging()

    configure_logging(verbose)

    repo_owner, repo_name = repo_owner_and_name.split("/")
    g = Github(github_token)
    repo = g.get_repo(repo_owner_and_name)

    end_date = datetime.now(tz=UTC).date()
    start_date = end_date - timedelta(days=7)

    pull_requests = summarize_prs(
        repo_owner,
        repo_name,
        datetime.combine(start_date, datetime.min.time(), tzinfo=UTC),
        datetime.combine(end_date, datetime.max.time(), tzinfo=UTC),
    )

    if pull_requests:
        template_content = importlib.resources.read_text(
            "repo_summary_post",
            "pr_summary_template.j2",
        )
        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(template_content)
        body = template.render(
            start_date=start_date,
            end_date=end_date - timedelta(days=1),
            pull_requests=pull_requests,
        )

        ai_summary = generate_ai_summary(
            body, model, start_date, end_date - timedelta(days=1)
        )

        body_with_summary = template.render(
            start_date=start_date,
            end_date=end_date - timedelta(days=1),
            pull_requests=pull_requests,
            ai_summary=ai_summary,
        )

        if args.output_content:
            write_output(body_with_summary, args.output_content)

        if args.output or args.output is None:
            write_output(ai_summary, args.output)

        if category and not dry_run:
            create_discussion(repo, "Recent activity", body_with_summary, category)
        elif category and dry_run:
            actions.core.info("Dry run mode: Discussion would have been created.")
        else:
            actions.core.info(
                "No category provided or dry run mode. Discussion not created."
            )
    else:
        actions.core.info("No PR activity in the past week.")


@measure_time
def generate_ai_summary(
    body: str, model_name: str, start_date: date, end_date: date
) -> str:
    """Generate an AI summary of the pull requests."""
    model = llm.get_model(model_name)
    if model.needs_key:
        model.key = get_key(None, model.needs_key, model.key_env_var)

    prompt_template = importlib.resources.read_text(
        "repo_summary_post", "llm_prompt.txt"
    )
    prompt = prompt_template.format(body=body)

    response = model.prompt(prompt)
    url = f"https://github.com/akaihola/repo-summary-post/tree/v{__version__}"
    metadata = {
        "start_date": str(start_date),
        "end_date": str(end_date),
        "powered_by": url,
        "llm": model_name,
    }

    template_content = importlib.resources.read_text(
        "repo_summary_post",
        "ai_summary_template.j2",
    )
    template = Template(template_content)
    return template.render(ai_summary=response.text(), metadata=metadata)


if __name__ == "__main__":
    main()
