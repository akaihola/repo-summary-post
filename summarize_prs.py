"""Module for summarizing GitHub Pull Requests and creating a discussion."""

import os
from datetime import UTC, datetime, timedelta

import actions.core  # alternative: https://pypi.org/project/actions-toolkit/
from github import BadCredentialsException, Github, GithubException
from github.Repository import Repository
from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport


def summarize_prs(
    client: Client,
    repo_owner: str,
    repo_name: str,
    start_date: datetime,
    end_date: datetime,
) -> list[str]:
    """Summarize Pull Requests within a given date range using GraphQL.

    Args:
    ----
        client: The GraphQL client.
        repo_owner: The owner of the repository.
        repo_name: The name of the repository.
        start_date: The start date of the summary period.
        end_date: The end date of the summary period.

    Returns:
    -------
        A list of formatted strings summarizing the Pull Requests.

    """
    query = gql(
        """
        query ($owner: String!,
               $name: String!,
               $startDate: DateTime!,
               $endDate: DateTime!,
               $after: String) {
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
              }
            }
          }
        }
        """,
    )

    variables = {
        "owner": repo_owner,
        "name": repo_name,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
    }

    summary = []
    has_next_page = True
    after = None

    while has_next_page:
        variables["after"] = after
        result = client.execute(query, variable_values=variables)
        prs = result["repository"]["pullRequests"]

        for pr in prs["nodes"]:
            pr_updated_at = datetime.fromisoformat(pr["updatedAt"].rstrip("Z")).replace(
                tzinfo=UTC,
            )
            if start_date <= pr_updated_at <= end_date:
                status = "merged" if pr["merged"] else pr["state"].lower()
                summary.append(f"- [{pr['title']}]({pr['url']}) ({status})")
            elif pr_updated_at < start_date:
                has_next_page = False
                break

        if has_next_page:
            has_next_page = prs["pageInfo"]["hasNextPage"]
            after = prs["pageInfo"]["endCursor"]

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
    repo_owner_and_name = os.environ["INPUT_REPO_NAME"]
    category = os.environ.get("INPUT_CATEGORY")
    title_template = os.environ.get(
        "INPUT_TITLE_TEMPLATE",
        "Recent activity (from {start} to {end})",
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
        body = (
            f"Here's a summary of Pull Request activity from the past week:\n\n"
            f"{'\n'.join(pr_summary)}"
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
