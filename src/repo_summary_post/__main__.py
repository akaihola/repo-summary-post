"""Module for summarizing GitHub Pull Requests and creating a discussion."""

from __future__ import annotations

import argparse
import importlib.resources
import os
from datetime import UTC, datetime, timedelta

import actions.core  # alternative: https://pypi.org/project/actions-toolkit/
import llm  # type: ignore[import]
import requests_cache
from github import Github
from gql import Client
from gql.transport.requests import RequestsHTTPTransport
from jinja2 import BaseLoader, Environment
from llm import get_key

from repo_summary_post.github_utils import create_discussion, summarize_prs


def main() -> None:
    """Summarize PRs and create a discussion if category is provided."""
    parser = argparse.ArgumentParser(description="Summarize GitHub Pull Requests")
    parser.add_argument(
        "--cache", action="store_true", help="Enable caching for GraphQL queries"
    )
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
        transport.client = requests_cache.CachedSession("github_cache")
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

        show_discussion_content(body_with_summary)

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
            "important changes, and any notable patterns in the pull requests."
        )

        response = model.prompt(prompt)
        return str(response.text())
    except Exception as e:
        error_message = str(e).lower()
        if "api_key" in error_message or "authentication" in error_message:
            actions.core.error(
                "Error: OpenRouter API key not set or invalid. Please set the OPENROUTER_API_KEY environment variable."
            )
        else:
            actions.core.error(f"Error generating AI summary: {e}")
        return "Unable to generate AI summary due to an error."


if __name__ == "__main__":
    main()
