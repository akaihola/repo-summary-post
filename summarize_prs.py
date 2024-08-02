"""Module for summarizing GitHub Pull Requests and creating a discussion."""

import os
from datetime import UTC, datetime, timedelta

import actions.core  # alternative: https://pypi.org/project/actions-toolkit/
from github import BadCredentialsException, Github, GithubException
from github.Repository import Repository


def summarize_prs(
    repo: Repository,
    start_date: datetime,
    end_date: datetime,
) -> list[str]:
    """Summarize Pull Requests within a given date range.

    Args:
    ----
        repo: The GitHub repository object.
        start_date: The start date of the summary period.
        end_date: The end date of the summary period.

    Returns:
    -------
        A list of formatted strings summarizing the Pull Requests.

    """
    prs = repo.get_pulls(state="all", sort="updated", direction="desc")
    summary = []

    for pr in prs:
        actions.core.debug(
            f"PR #{pr.number}: "
            f"Title: {pr.title}, "
            f"Updated: {pr.updated_at}, "
            f"State: {pr.state}",
        )
        if start_date <= pr.updated_at <= end_date:
            status = (
                "merged" if pr.merged else "closed" if pr.state == "closed" else "open"
            )
            summary.append(f"- [{pr.title}]({pr.html_url}) ({status})")
        elif pr.updated_at < start_date:
            break

    return summary


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


def show_discussion_content(title: str, body: str) -> None:
    """Show the content of the discussion that would be created.

    Args:
    ----
        title: The title of the discussion.
        body: The body content of the discussion.

    """
    actions.core.info("Discussion content:")
    actions.core.info(f"Title: {title}")
    actions.core.info("Body:")
    actions.core.info(body)


def main() -> None:
    """Summarize PRs and create a discussion if category is provided."""
    github_token = os.environ["INPUT_GITHUB_TOKEN"]
    repo_name = os.environ["INPUT_REPO_NAME"]
    category = os.environ.get("INPUT_CATEGORY")

    g = Github(github_token)
    repo = g.get_repo(repo_name)

    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=7)

    pr_summary = summarize_prs(repo, start_date, end_date)

    if pr_summary:
        title = f"Weekly PR Summary ({start_date.date()} to {end_date.date()})"
        body = (
            "Here's a summary of Pull Request activity from the past week:\n\n"
            + "\n".join(pr_summary)
        )
        show_discussion_content(title, body)

        if category:
            create_discussion(repo, title, body, category)
        else:
            actions.core.info("No category provided. Discussion not created.")
    else:
        actions.core.info("No PR activity in the past week.")


if __name__ == "__main__":
    main()
