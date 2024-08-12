"""Module for summarizing GitHub activity and creating a summary discussion post."""

from __future__ import annotations

import importlib.resources
import logging
import re
import sys
import time
from argparse import SUPPRESS, ArgumentParser, Namespace
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any

import actions
import llm  # type: ignore[import-untyped]
from github import Github
from jinja2 import BaseLoader, Environment, Template
from llm import get_key

from repo_summary_post import __version__
from repo_summary_post.github_utils import (
    count_comments,
    count_commits,
    create_discussion,
    find_newest_summaries,
    summarize_prs_issues_releases_and_discussions,
)
from repo_summary_post.logging_utils import configure_logging

if TYPE_CHECKING:
    from collections.abc import Callable


def get_config(args: Namespace, input_name: str, default: Any = SUPPRESS) -> Any:
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
    elif isinstance(default, int):
        input_value = actions.core.get_input(input_name, required=required)
        return int(input_value) if input_value else default
    return actions.core.get_input(input_name, required=required) or default


def write_output(content: str, title: str | None, output_path: str | None) -> None:
    """Write content with title to a file or stdout."""
    if title:
        full_content = f"{title}\n\n{content}"
    else:
        full_content = content
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


def have_enough_content(activities):
    return (
        len(activities) >= 2
        and count_comments(activities) + count_commits(activities) >= 2
    )


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
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
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
    dry_run = get_config(args, "dry_run", False)
    output_content = get_config(args, "output_content", None)
    output = get_config(args, "output", None)
    output_prompt = get_config(args, "output_prompt", None)

    if not github_token:
        actions.core.error(
            "GitHub token is required."
            " Please provide it via --github-token or 'github-token' input."
        )
        sys.exit(1)

    if not repo_owner_and_name:
        actions.core.error(
            "Repository name is required."
            " Please provide it via --repo-name or 'repo-name' input."
        )
        sys.exit(1)

    configure_logging(verbose)

    repo_owner, repo_name = repo_owner_and_name.split("/")
    g = Github(github_token)
    repo = g.get_repo(repo_owner_and_name)

    previous_summaries = (
        find_newest_summaries(repo, category, 3, use_cache=args.cache)
        if category
        else []
    )
    if args.start:
        start_date = args.start
        actions.core.info(f"Using provided start date: {start_date}")
    else:
        # Find the newest previous summaries
        if previous_summaries:
            start_date = previous_summaries[0][0] + timedelta(days=1)
            actions.core.info(f"Continuing summary after previous one: {start_date}")
        else:
            start_date = repo.created_at.date()
            actions.core.info(
                f"Starting summary at repository creation day: {start_date}"
            )

    # Ensure start_date is not more than 7 days before end_date
    end_date = start_date
    activities = []
    today = datetime.now(tz=UTC).date()
    while not have_enough_content(activities) and end_date < today:
        end_date = min(today, end_date + timedelta(days=7))
        activities = summarize_prs_issues_releases_and_discussions(
            repo_owner,
            repo_name,
            datetime.combine(start_date, datetime.min.time(), tzinfo=UTC),
            datetime.combine(end_date, datetime.max.time(), tzinfo=UTC),
            use_cache=args.cache,
        )
        logging.debug(
            "Found %d PRs/issues/releases/discussions between %s and %s",
            len(activities),
            start_date,
            end_date,
        )

    if not have_enough_content(activities):
        actions.core.info(
            "Not enough content to summarize. Skipping discussion creation."
        )
        return

    # actual end date is the following midnight, but we want to show the previous day
    # in the UI
    ui_end_date = end_date - timedelta(days=1)

    # Extract the summary texts from previous_summaries
    previous_summary_texts = [
        "\n\n".join(
            [
                f"{title}",
                re.sub(r"---\n\n<details>.*$", "", summary, flags=re.DOTALL),
            ]
        )
        for _, title, summary in previous_summaries
    ]

    template_content = importlib.resources.read_text(
        "repo_summary_post",
        "pr_summary_template.j2",
    )
    env = Environment(loader=BaseLoader(), autoescape=False)
    template = env.from_string(template_content)
    activity_report = template.render(
        project_name=project_name,
        start_date=start_date,
        end_date=ui_end_date,
        items=activities,
    )

    prompt_template_content = importlib.resources.read_text(
        "repo_summary_post", "llm_prompt.j2"
    )
    prompt_template = env.from_string(prompt_template_content)
    prompt = prompt_template.render(
        body=activity_report,
        previous_summaries=previous_summary_texts,
        project_name=project_name,
        start_date=start_date,
        end_date=ui_end_date,
    )

    # Log the project_name for debugging
    logging.debug(f"Project name: {project_name}")

    title, ai_summary = generate_ai_summary(model, start_date, ui_end_date, prompt)

    if output_content:
        write_output(activity_report, title=None, output_path=output_content)

    if output or output is None:
        write_output(ai_summary, title=title, output_path=output)

    if output_prompt:
        write_output(prompt, title=None, output_path=output_prompt)

    if category and not dry_run:
        create_discussion(repo, title, ai_summary, category)
    elif category and dry_run:
        actions.core.info(
            f"Dry run mode: Discussion with title '{title}' would have been created."
        )
    else:
        actions.core.info(
            "No category provided or dry run mode. Discussion not created."
        )


@measure_time
def generate_ai_summary(
    model_name: str, start_date: date, end_date: date, prompt: str
) -> tuple[str, str]:
    """Generate an AI summary of recent activity."""
    model = llm.get_model(model_name)
    if model.needs_key:
        model.key = get_key(None, model.needs_key, model.key_env_var)

    response = model.prompt(prompt)
    response_text = response.text()
    title, content = response_text.split("\n", 1)
    url = f"https://github.com/akaihola/repo-summary-post/tree/v{__version__}"
    metadata = {
        "start_date": str(start_date),
        "end_date": str(end_date),  # this is the UI end date
        "powered_by": url,
        "llm": model_name,
    }

    template_content = importlib.resources.read_text(
        "repo_summary_post",
        "ai_summary_template.j2",
    )
    template = Template(template_content)
    return title.strip(), template.render(ai_summary=content.strip(), metadata=metadata)


if __name__ == "__main__":
    main()
