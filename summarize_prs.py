"""Module for summarizing GitHub Pull Requests and creating a discussion."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from textwrap import indent
from typing import TYPE_CHECKING, Any

import actions.core  # alternative: https://pypi.org/project/actions-toolkit/
from github import BadCredentialsException, Github, GithubException
from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport

if TYPE_CHECKING:
    from collections.abc import Iterator

    from github.Repository import Repository


def main() -> None:
    """Summarize PRs and create a discussion if category is provided."""
    github_token = os.environ["INPUT_GITHUB_TOKEN"]
    repo_owner_and_name = os.environ["INPUT_REPO_NAME"]
    category = os.environ.get("INPUT_CATEGORY")
    title_template = os.environ.get(
        "INPUT_TITLE_TEMPLATE",
        "# Recent activity (from {start} to {end})",
    )

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

    pr_summary = summarize_prs(
        client,
        repo_owner,
        repo_name,
        datetime.combine(start_date, datetime.min.time(), tzinfo=UTC),
        datetime.combine(end_date, datetime.max.time(), tzinfo=UTC),
    )

    if pr_summary:
        title = title_template.format(
            start=start_date,
            end=end_date - timedelta(days=1),
        )
        body = "\n".join(pr_summary)
        show_discussion_content(title, body)

        if category:
            create_discussion(repo, title, body, category)
        else:
            actions.core.info("No category provided. Discussion not created.")
    else:
        actions.core.info("No PR activity in the past week.")


def fetch_pull_requests(
    client: Client,
    repo_owner: str,
    repo_name: str,
) -> Iterator[dict[str, Any]]:
    """Fetch Pull Requests using GraphQL, handling pagination."""
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
              }
            }
          }
        }
        """,
    )

    variables = {
        "owner": repo_owner,
        "name": repo_name,
    }

    has_next_page = True
    after = None

    while has_next_page:
        variables["after"] = after
        result = client.execute(query, variable_values=variables)
        prs = result["repository"]["pullRequests"]
        yield from prs["nodes"]
        has_next_page = prs["pageInfo"]["hasNextPage"]
        after = prs["pageInfo"]["endCursor"]


def summarize_prs(
    client: Client,
    repo_owner: str,
    repo_name: str,
    start_date: datetime,
    end_date: datetime,
) -> list[str]:
    """Summarize Pull Requests within a given date range using GraphQL."""
    summary = []

    for pr in fetch_pull_requests(client, repo_owner, repo_name):
        pr_updated_at = datetime.fromisoformat(pr["updatedAt"].rstrip("Z")).replace(
            tzinfo=UTC,
        )
        if start_date <= pr_updated_at <= end_date:
            summary.extend(
                process_pr(client, repo_owner, repo_name, pr, start_date, end_date),
            )
        elif pr_updated_at < start_date:
            break

    return summary


def fetch_comments(
    client: Client,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
) -> Iterator[dict[str, Any]]:
    """Fetch comments for a pull request using GraphQL, handling pagination."""
    query = gql(
        """
        query ($owner: String!, $name: String!, $number: Int!, $after: String) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              comments(first: 100, after: $after) {
                pageInfo {
                  hasNextPage
                  endCursor
                }
                nodes {
                  createdAt
                  body
                }
              }
            }
          }
        }
        """,
    )

    variables = {
        "owner": repo_owner,
        "name": repo_name,
        "number": pr_number,
    }

    has_next_page = True
    after = None

    while has_next_page:
        variables["after"] = after
        result = client.execute(query, variable_values=variables)
        comments = result["repository"]["pullRequest"]["comments"]
        yield from comments["nodes"]
        has_next_page = comments["pageInfo"]["hasNextPage"]
        after = comments["pageInfo"]["endCursor"]


def process_pr(
    client: Client,
    repo_owner: str,
    repo_name: str,
    pr: dict[str, Any],
    start_date: datetime,
    end_date: datetime,
) -> list[str]:
    """Process a single pull request and return its summary."""
    status = "merged" if pr["merged"] else pr["state"].lower()
    pr_summary = [
        f"## Pull request #{pr['number']}: {pr['title']} ({status})",
        "",
    ]

    if pr.get("body"):
        pr_summary.extend([indent(pr["body"], "    "), ""])

    old_comments, recent_comments = process_comments(
        client,
        repo_owner,
        repo_name,
        pr,
        start_date,
        end_date,
    )

    if old_comments:
        pr_summary.extend(
            [
                f"### OLD COMMENTS to pull request #{pr['number']}"
                f" BEFORE {start_date.date()}",
                "",
                *old_comments,
                "",
            ],
        )
    if recent_comments:
        pr_summary.extend(
            [
                f"### RECENT COMMENTS to pull request #{pr['number']}"
                f" BETWEEN {start_date.date()} and {end_date.date()}",
                "",
                *recent_comments,
                "",
            ],
        )

    return pr_summary


def process_comments(
    client: Client,
    repo_owner: str,
    repo_name: str,
    pr: dict[str, Any],
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[str], list[str]]:
    """Process comments for a pull request and return old and recent comments."""
    old_comments = []
    recent_comments = []

    for comment in fetch_comments(
        client,
        repo_owner,
        repo_name,
        pr["number"],
    ):
        comment_date = datetime.fromisoformat(comment["createdAt"].rstrip("Z")).replace(
            tzinfo=UTC,
        )
        comment_lines = [
            f"#### Pull request #{pr['number']} / comment from @<author_placeholder>",
            "",
            indent(comment["body"].strip(), "    "),
            "",
        ]
        if comment_date < start_date:
            old_comments.extend(comment_lines)
        elif start_date <= comment_date <= end_date:
            recent_comments.extend(comment_lines)

    return old_comments, recent_comments


def show_discussion_content(title: str, body: str) -> None:
    """Show the content of the discussion that would be created.

    Args:
    ----
        title: The title of the discussion.
        body: The body content of the discussion.

    """
    actions.core.info("Discussion content:")
    actions.core.info(title)
    actions.core.info("")
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
