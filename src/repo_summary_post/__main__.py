"""Module for summarizing GitHub Pull Requests and creating a discussion."""

from __future__ import annotations

import argparse
import importlib.resources
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from functools import wraps
from textwrap import dedent
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import actions.core  # alternative: https://pypi.org/project/actions-toolkit/
import llm  # type: ignore[import-untyped]
from github import Github
from jinja2 import BaseLoader, Environment
from llm import get_key

from repo_summary_post.caching import configure_caching_logging
from repo_summary_post.github_utils import create_discussion, summarize_prs


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
        help="LLM model to use for generating the summary",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (use -v for INFO, -vv for DEBUG)",
    )
    args = parser.parse_args()

    configure_logging(args.verbose)

    github_token = os.environ["INPUT_GITHUB_TOKEN"]
    repo_owner_and_name = os.environ["INPUT_REPO_NAME"]
    category = os.environ.get("INPUT_CATEGORY")

    if args.cache:
        configure_caching_logging()

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

        ai_summary = generate_ai_summary(body, args.model)

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

        if category:
            create_discussion(repo, "Recent activity", body_with_summary, category)
        else:
            actions.core.info("No category provided. Discussion not created.")
    else:
        actions.core.info("No PR activity in the past week.")


@measure_time
def generate_ai_summary(body: str, model_name: str) -> str:
    """Generate an AI summary of the pull requests."""
    try:
        model = llm.get_model(model_name)
        if model.needs_key:
            model.key = get_key(None, model.needs_key, model.key_env_var)

        prompt = dedent(
            f"""
            You are a helpful assistant that summarizes GitHub pull request activity.

            Summarize the following GitHub pull request activity report:

            f{body}

            Provide a concise summary of the overall activity, highlighting key trends,
            important changes, and any notable patterns in the pull requests.
            Keep the summary under 200 words.
            """
        )

        response = model.prompt(prompt)
        return str(response.text())
    except (llm.LLMError, ValueError) as e:
        error_message = str(e).lower()
        if "api_key" in error_message or "authentication" in error_message:
            actions.core.error(
                "Error: OpenRouter API key not set or invalid. "
                "Please set the OPENROUTER_API_KEY environment variable.",
            )
        else:
            actions.core.error("Error generating AI summary: %s", e)
        return "Unable to generate AI summary due to an error."
    except Exception as e:
        actions.core.error("Unexpected error generating AI summary: %s", e)
        return "Unable to generate AI summary due to an unexpected error."


if __name__ == "__main__":
    main()
