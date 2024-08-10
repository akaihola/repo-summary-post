"""Module for summarizing GitHub Pull Requests and creating a discussion."""

from __future__ import annotations

import importlib.resources
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import actions.core  # alternative: https://pypi.org/project/actions-toolkit/
import llm  # type: ignore[import]
from github import Github
from gql import Client
from gql.transport.requests import RequestsHTTPTransport
from jinja2 import BaseLoader, Environment

from repo_summary_post.github_utils import create_discussion, summarize_prs


def main() -> None:
    """Summarize PRs and create a discussion if category is provided."""
    github_token = os.environ["INPUT_GITHUB_TOKEN"]
    repo_owner_and_name = os.environ["INPUT_REPO_NAME"]
    category = os.environ.get("INPUT_CATEGORY")

    transport = RequestsHTTPTransport(
        url="https://api.github.com/graphql",
        headers={"Authorization": f"Bearer {github_token}"},
        use_json=True,
    )
    client = Client(transport=transport, fetch_schema_from_transport=True)

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
        ai_summary = generate_ai_summary(pull_requests)

        template_content = importlib.resources.read_text(
            "repo_summary_post", "pr_summary_template.j2",
        )
        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(template_content)
        body = template.render(
            start_date=start_date,
            end_date=end_date - timedelta(days=1),
            pull_requests=pull_requests,
            ai_summary=ai_summary,
        )
        show_discussion_content(body)

        if category:
            create_discussion(repo, "Recent activity", body, category)
        else:
            actions.core.info("No category provided. Discussion not created.")
    else:
        actions.core.info("No PR activity in the past week.")


def show_discussion_content(body: str) -> None:
    """Show the content of the discussion that would be created."""
    actions.core.info("Discussion content:")
    actions.core.info(body)


def generate_ai_summary(pull_requests: list[dict[str, Any]]) -> str:
    """Generate an AI summary of the pull requests."""
    # Ensure the OPENROUTER_KEY environment variable is set
    error_message = "OPENROUTER_KEY environment variable is not set"
    if "OPENROUTER_KEY" not in os.environ:
        raise ValueError(error_message)

    model = llm.get_model("openrouter/anthropic/claude-3.5-sonnet:beta")

    prompt = (
        "You are a helpful assistant that summarizes GitHub pull request activity.\n\n"
    )
    prompt += "Summarize the following GitHub pull requests:\n\n"
    for pr in pull_requests:
        prompt += f"- #{pr['number']}: {pr['title']} ({pr['status']})\n"

    response = model.prompt(prompt)

    return str(response.text())


if __name__ == "__main__":
    main()
