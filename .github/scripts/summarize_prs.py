"""Module for summarizing GitHub Pull Requests and creating a discussion."""

import os
from datetime import datetime, timedelta, timezone
from typing import List

from github import Github
from github.Repository import Repository


def summarize_prs(
    repo: Repository, start_date: datetime, end_date: datetime
) -> List[str]:
    """
    Summarize Pull Requests within a given date range.

    Args:
        repo: The GitHub repository object.
        start_date: The start date of the summary period.
        end_date: The end date of the summary period.

    Returns:
        A list of formatted strings summarizing the Pull Requests.
    """
    prs = repo.get_pulls(state="all", sort="updated", direction="desc")
    summary = []

    for pr in prs:
        if start_date <= pr.updated_at <= end_date:
            status = (
                "merged" if pr.merged else "closed" if pr.state == "closed" else "open"
            )
            summary.append(f"- [{pr.title}]({pr.html_url}) ({status})")
        elif pr.updated_at < start_date:
            break

    return summary


def create_discussion(repo: Repository, title: str, body: str) -> None:
    """
    Create a discussion in the repository.

    Args:
        repo: The GitHub repository object.
        title: The title of the discussion.
        body: The body content of the discussion.
    """
    try:
        repo.create_discussion(title=title, body=body, category="General")
        print("Discussion created successfully.")
    except Exception as e:
        print(f"Error creating discussion: {e!s}")


def main() -> None:
    """Main function to summarize PRs and create a discussion."""
    github_token = os.environ["GITHUB_TOKEN"]
    repo_name = os.environ["REPO_NAME"]

    g = Github(github_token)
    repo = g.get_repo(repo_name)

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=7)

    pr_summary = summarize_prs(repo, start_date, end_date)

    if pr_summary:
        title = f"Weekly PR Summary ({start_date.date()} to {end_date.date()})"
        body = (
            "Here's a summary of Pull Request activity from the past week:\n\n"
            + "\n".join(pr_summary)
        )
        create_discussion(repo, title, body)
    else:
        print("No PR activity in the past week.")


if __name__ == "__main__":
    main()
