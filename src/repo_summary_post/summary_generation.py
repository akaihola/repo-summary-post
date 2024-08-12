"""Module for generating AI summaries and creating discussions."""

from __future__ import annotations

import importlib.resources
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

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


def have_enough_content(activities: list[dict[str, Any]]) -> bool:
    return (
        len(activities) >= 2
        and count_comments(activities) + count_commits(activities) >= 2
    )


def generate_summary(
    github_token: str,
    repo_owner_and_name: str,
    project_name: str,
    category: str,
    model: str,
    start_date: date | None,
    *,
    use_cache: bool,
    dry_run: bool,
) -> tuple[str, str, str, str]:
    """Generate summary of GitHub activity and create a discussion."""
    g = Github(github_token)
    repo = g.get_repo(repo_owner_and_name)
    repo_owner, repo_name = repo_owner_and_name.split("/")

    previous_summaries = (
        find_newest_summaries(repo, category, 3, use_cache=use_cache)
        if category
        else []
    )
    if start_date is None:
        if previous_summaries:
            start_date = previous_summaries[0][0] + timedelta(days=1)
            actions.core.info(f"Continuing summary after previous one: {start_date}")
        else:
            start_date = repo.created_at.date()
            actions.core.info(
                f"Starting summary at repository creation day: {start_date}",
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
            use_cache=use_cache,
        )
        logging.debug(
            "Found %d PRs/issues/releases/discussions between %s and %s",
            len(activities),
            start_date,
            end_date,
        )

    if not have_enough_content(activities):
        actions.core.info(
            "Not enough content to summarize. Skipping discussion creation.",
        )
        return "", "", ""

    # actual end date is the following midnight, but we want to show the previous day
    # in the UI
    ui_end_date = end_date - timedelta(days=1)

    # Extract the summary texts from previous_summaries
    previous_summary_texts = [
        "\n\n".join(
            [
                f"{title}",
                re.sub(r"---\n\n<details>.*$", "", summary, flags=re.DOTALL),
            ],
        )
        for _, title, summary in previous_summaries
    ]

    template_content = importlib.resources.read_text(
        "repo_summary_post",
        "pr_summary_template.j2",
    )
    env = Environment(loader=BaseLoader(), autoescape=False)  # noqa: S701
    template = env.from_string(template_content)
    activity_report = template.render(
        project_name=project_name,
        start_date=start_date,
        end_date=ui_end_date,
        items=activities,
    )

    prompt = generate_prompt(
        activity_report,
        previous_summary_texts,
        project_name,
        start_date,
        ui_end_date,
        model,
    )

    # Log the project_name for debugging
    logging.debug("Project name: %s", project_name)

    title, ai_summary = generate_ai_summary(model, start_date, ui_end_date, prompt)

    if category and not dry_run:
        create_discussion(repo, title, ai_summary, category)
    elif category and dry_run:
        actions.core.info(
            f"Dry run mode: Discussion with title '{title}' would have been created.",
        )
    else:
        actions.core.info(
            "No category provided or dry run mode. Discussion not created.",
        )

    return activity_report, title, ai_summary, prompt


def generate_ai_summary(
    model_name: str,
    start_date: date,
    end_date: date,
    prompt: str,
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


def generate_prompt(
    activity_report: str,
    previous_summary_texts: list[str],
    project_name: str,
    start_date: date,
    end_date: date,
    model_name: str,
) -> str:
    """Generate the prompt for the AI summary."""
    prompt_template_content = importlib.resources.read_text(
        "repo_summary_post",
        "llm_prompt.j2",
    )
    env = Environment(loader=BaseLoader(), autoescape=False)  # noqa: S701
    prompt_template = env.from_string(prompt_template_content)
    return prompt_template.render(
        body=activity_report,
        previous_summaries=previous_summary_texts,
        project_name=project_name,
        start_date=start_date,
        end_date=end_date,
        model_name=model_name,
    )
