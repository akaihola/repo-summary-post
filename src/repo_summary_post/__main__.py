"""Module for summarizing GitHub Pull Requests and creating a discussion."""

from __future__ import annotations

import argparse
import importlib.resources
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Callable

import actions.core  # alternative: https://pypi.org/project/actions-toolkit/
import llm  # type: ignore[import-untyped]
from github import Github
from gql import Client
from gql.transport.requests import RequestsHTTPTransport
from jinja2 import BaseLoader, Environment
from llm import get_key
from requests_cache.session import CachedSession

from repo_summary_post.github_utils import create_discussion, summarize_prs


def write_to_file(content: str, file_path: str) -> None:
    """Write content to a file."""
    try:
        from pathlib import Path

        Path(file_path).write_text(content, encoding="utf-8")
        actions.core.info(f"Content written to {file_path}")
    except OSError as e:
        actions.core.error(f"Error writing to file {file_path}: {e}")


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


def configure_logging():
    """Configure logging for the application."""
    logging.basicConfig(level=logging.DEBUG)
    requests_cache_logger = logging.getLogger("requests_cache")
    requests_cache_logger.setLevel(logging.DEBUG)
    requests_logger = logging.getLogger("requests")
    requests_logger.setLevel(logging.DEBUG)
    logging.getLogger("gql.transport.requests").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)

    # Custom filter to exclude response content
    class ExcludeResponseFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            message = record.getMessage()
            return not message.strip().startswith("<<<")

    requests_cache_logger.addFilter(ExcludeResponseFilter())
    requests_logger.addFilter(ExcludeResponseFilter())

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
    args = parser.parse_args()

    github_token = os.environ["INPUT_GITHUB_TOKEN"]
    repo_owner_and_name = os.environ["INPUT_REPO_NAME"]
    category = os.environ.get("INPUT_CATEGORY")

    transport = RequestsHTTPTransport(
        url="https://api.github.com/graphql",
        headers={"Authorization": f"Bearer {github_token}"},
        use_json=True,
    )
    if args.cache:
        configure_logging()

        # Create CachedSession with debug output
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

            def create_key(request):
                return f"{request.method}:{request.url}:{request.body}"

            cached_session.cache.create_key = create_key
        requests_cache_logger.debug("CachedSession created")
        if hasattr(transport, "session"):
            transport.session = cached_session  # type: ignore

        # Enable request logging
        urllib3_logger = logging.getLogger("urllib3")
        urllib3_logger.setLevel(logging.DEBUG)
        urllib3_logger.propagate = True
        urllib3_logger.addFilter(ExcludeResponseFilter())

    client = Client(
        transport=transport,
        fetch_schema_from_transport=False,
        execute_timeout=30,
    )

    repo_owner, repo_name = repo_owner_and_name.split("/")
    g = Github(github_token)
    repo = g.get_repo(repo_owner_and_name)

    end_date = datetime.now(tz=UTC).date()
    start_date = end_date - timedelta(days=7)

    pull_requests = summarize_prs(
        client,
        repo_owner,
        repo_name,
        datetime.combine(start_date, datetime.min.time(), tzinfo=UTC),
        datetime.combine(end_date, datetime.max.time(), tzinfo=UTC),
    )

    if pull_requests:
        template_content = importlib.resources.read_text(
            "repo_summary_post", "pr_summary_template.j2",
        )
        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(template_content)
        body = template.render(
            start_date=start_date,
            end_date=end_date - timedelta(days=1),
            pull_requests=pull_requests,
        )

        ai_summary = generate_ai_summary(body)

        body_with_summary = template.render(
            start_date=start_date,
            end_date=end_date - timedelta(days=1),
            pull_requests=pull_requests,
            ai_summary=ai_summary,
        )

        if args.output_content:
            write_to_file(body, args.output_content)
        else:
            show_discussion_content(body_with_summary)

        if args.output:
            write_to_file(ai_summary, args.output)

        if category:
            create_discussion(repo, "Recent activity", body_with_summary, category)
        else:
            actions.core.info("No category provided. Discussion not created.")
    else:
        actions.core.info("No PR activity in the past week.")


def show_discussion_content(body: str) -> None:
    """Show the content of the discussion that would be created."""
    actions.core.info("Discussion content:")
    actions.core.info(body)


@measure_time
def generate_ai_summary(body: str) -> str:
    """Generate an AI summary of the pull requests."""
    try:
        model = llm.get_model("openrouter/anthropic/claude-3.5-sonnet:beta")
        if model.needs_key:
            model.key = get_key(None, model.needs_key, model.key_env_var)

        prompt = (
            "You are a helpful assistant that summarizes GitHub pull request activity.\n\n"
            "Summarize the following GitHub pull request activity report:\n\n"
            f"{body}\n\n"
            "Provide a concise summary of the overall activity, highlighting key trends, "
            "important changes, and any notable patterns in the pull requests. "
            "Keep the summary under 200 words."
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
