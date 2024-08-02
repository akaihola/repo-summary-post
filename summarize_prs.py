"""Module for summarizing GitHub Pull Requests and creating a discussion."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import actions.core  # alternative: https://pypi.org/project/actions-toolkit/
from github import BadCredentialsException, Github, GithubException
from gql import Client, gql
from gql.transport.exceptions import TransportQueryError
from gql.transport.requests import RequestsHTTPTransport
from jinja2 import Environment, FileSystemLoader

if TYPE_CHECKING:
    from collections.abc import Iterator

    from github.Repository import Repository


@dataclass
class PRContext:
    """Context object for processing Pull Requests."""

    client: Client
    repo_owner: str
    repo_name: str
    start_date: datetime
    end_date: datetime


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
        env = Environment(loader=FileSystemLoader("."), autoescape=True)
        template = env.get_template("pr_summary_template.j2")
        body = template.render(
            start_date=start_date,
            end_date=end_date - timedelta(days=1),
            pull_requests=pull_requests,
        )
        show_discussion_content(body)

        if category:
            create_discussion(repo, "Recent activity", body, category)
        else:
            actions.core.info("No category provided. Discussion not created.")
    else:
        actions.core.info("No PR activity in the past week.")


def fetch_pull_requests(
    client: Client,
    repo_owner: str,
    repo_name: str,
) -> Iterator[dict[str, Any]]:
    """Fetch Pull Requests and comments using GraphQL, handling pagination."""
    query = gql(
        """
        query ($owner: String!, $name: String!, $after: String) {
          repository(owner: $owner, name: $name) {
            pullRequests(first: 100,
                         orderBy: {field: UPDATED_AT, direction: DESC},
                         after: $after) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                number
                title
                url
                updatedAt
                state
                merged
                body
                comments(first: 100) {
                  nodes {
                    createdAt
                    body
                  }
                }
              }
            }
          }
        }
        """,
    )

    variables: dict[str, Any] = {
        "owner": repo_owner,
        "name": repo_name,
        "after": None,
    }

    has_next_page = True

    while has_next_page:
        try:
            result = client.execute(query, variable_values=variables)
            prs = result["repository"]["pullRequests"]
        except TransportQueryError as e:
            actions.core.error(f"GraphQL query failed: {e}")
            break
        for pr in prs["nodes"]:
            pr["comments"] = pr["comments"]["nodes"]
            yield pr
        has_next_page = prs["pageInfo"]["hasNextPage"]
        variables["after"] = prs["pageInfo"]["endCursor"]


def summarize_prs(
    client: Client,
    repo_owner: str,
    repo_name: str,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Summarize Pull Requests within a given date range using GraphQL."""
    summary = []
    context = PRContext(client, repo_owner, repo_name, start_date, end_date)

    for pr in fetch_pull_requests(client, repo_owner, repo_name):
        pr_updated_at = datetime.fromisoformat(pr["updatedAt"].rstrip("Z")).replace(
            tzinfo=UTC,
        )
        if start_date <= pr_updated_at <= end_date:
            summary.append(process_pr(context, pr))
        elif pr_updated_at < start_date:
            break

    return summary


def fetch_comments(pr: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Fetch comments for a pull request from the PR data."""
    yield from pr["comments"]


def process_pr(
    context: PRContext,
    pr: dict[str, Any],
) -> dict[str, Any]:
    """Process a single pull request and return its summary."""
    status = "merged" if pr["merged"] else pr["state"].lower()

    old_comments, recent_comments = process_comments(context, pr)

    return {
        "number": pr["number"],
        "title": pr["title"],
        "status": status,
        "body": pr.get("body"),
        "old_comments": old_comments,
        "recent_comments": recent_comments,
    }


def process_comments(
    context: PRContext,
    pr: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Process comments for a pull request and return old and recent comments."""
    old_comments = []
    recent_comments = []

    for comment in fetch_comments(pr):
        comment_date = datetime.fromisoformat(comment["createdAt"].rstrip("Z")).replace(
            tzinfo=UTC,
        )
        comment_data = {
            "body": comment["body"].strip(),
        }
        if comment_date < context.start_date:
            old_comments.append(comment_data)
        elif context.start_date <= comment_date <= context.end_date:
            recent_comments.append(comment_data)

    return old_comments, recent_comments


def show_discussion_content(body: str) -> None:
    """Show the content of the discussion that would be created.

    Args:
    ----
        body: The body content of the discussion.

    """
    actions.core.info("Discussion content:")
    actions.core.info(body)


def create_discussion(repo: Repository, title: str, body: str, category: str) -> None:
    """Create a discussion in the repository.

    Args:
    ----
        repo: The GitHub repository object.
        title: The title of the discussion.
        body: The body content of the discussion.
        category: The category of the discussion.

    """
    try:
        repo.create_discussion(title=title, body=body, category=category)
        actions.core.info("Discussion created successfully.")
    except (GithubException, BadCredentialsException) as e:
        actions.core.error(f"Error creating discussion: {e!s}")


if __name__ == "__main__":
    main()
