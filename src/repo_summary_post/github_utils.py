"""Utility functions for interacting with GitHub API."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

import actions.core
from cachetools import LRUCache, cached, keys
from gql import Client, gql
from gql.transport.exceptions import TransportQueryError
from gql.transport.requests import RequestsHTTPTransport

from repo_summary_post.caching import cached_execute

if TYPE_CHECKING:
    from collections.abc import Callable

    from github.Repository import Repository

T = TypeVar("T")


def parse_date(date_str: str) -> datetime:
    """Parse a date string in ISO format."""
    return datetime.fromisoformat(date_str.rstrip("Z")).replace(tzinfo=UTC)


# Create an LRU cache with a maximum size of 100 items
query_cache = LRUCache(maxsize=100)


def cache_key(
    query: Any,  # noqa: ANN401
    variables: Dict[str, Any],
    **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    """Create a cache key from the function arguments."""
    return keys.hashkey(query.loc.source.body, json.dumps(variables, sort_keys=True))


@cached(query_cache, key=cache_key)  # in-memory cache always enabled
def execute_query(
    query: Any,  # noqa: ANN401
    variables: Dict[str, Any],
    use_cache: bool = False,
) -> Dict[str, Any]:
    """Execute a GraphQL query with optional caching."""
    if use_cache:  # meaning the persisted disk cache
        return cached_execute(query, variables)
    else:
        transport = RequestsHTTPTransport(
            url="https://api.github.com/graphql",
            headers={"Authorization": f'Bearer {os.environ["INPUT_GITHUB_TOKEN"]}'},
        )
        client = Client(transport=transport, fetch_schema_from_transport=True)
        return client.execute(query, variable_values=variables)


def measure_time(func: Callable[..., T]) -> Callable[..., T]:
    """Measure the execution time of a function."""

    @wraps(func)
    def wrapper(*args: object, **kwargs: object) -> T:
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        duration = end_time - start_time
        logging.info("%s took %.2f seconds", func.__name__, duration)
        return result

    return wrapper


@dataclass
class ActivityContext:
    """Context object for processing PRs and issues."""

    repo_owner: str
    repo_name: str
    start_date: datetime
    end_date: datetime


def fetch_pull_requests_issues_releases_and_discussions(
    repo_owner: str,
    repo_name: str,
    start_date: date,
    use_cache: bool = False,
) -> list[dict[str, Any]]:
    """Fetch paginated PRs, Issues, Releases, Discussions and comments using GraphQL."""
    query = gql(
        """
        query ($owner: String!,
               $name: String!,
               $afterPR: String,
               $afterIssue: String,
               $afterRelease: String,
               $afterDiscussion: String) {
          repository(owner: $owner, name: $name) {
            pullRequests(first: 100,
                         orderBy: {field: UPDATED_AT, direction: DESC},
                         after: $afterPR) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                number
                title
                url
                createdAt
                updatedAt
                state
                merged
                mergedAt
                closedAt
                body
                comments(first: 100) {
                  nodes {
                    createdAt
                    body
                    author {
                      login
                    }
                  }
                }
                commits(last: 100) {
                  nodes {
                    commit {
                      message
                      committedDate
                      author {
                        name
                      }
                    }
                  }
                }
              }
            }
            issues(first: 100,
                   orderBy: {field: UPDATED_AT, direction: DESC},
                   after: $afterIssue) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                number
                title
                url
                createdAt
                updatedAt
                state
                closedAt
                body
                comments(first: 100) {
                  nodes {
                    createdAt
                    body
                    author {
                      login
                    }
                  }
                }
              }
            }
            releases(first: 100,
                     orderBy: {field: CREATED_AT, direction: DESC},
                     after: $afterRelease) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                name
                tagName
                createdAt
                description
                url
              }
            }
            discussions(first: 100,
                        orderBy: {field: UPDATED_AT, direction: DESC},
                        after: $afterDiscussion) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                number
                title
                body
                url
                closedAt
                createdAt
                updatedAt
                category {
                  name
                }
                body
                comments(first: 100) {
                  nodes {
                    createdAt
                    body
                    author {
                      login
                    }
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
        "afterPR": None,
        "afterIssue": None,
        "afterRelease": None,
        "afterDiscussion": None,
    }

    has_next_page_pr = True
    has_next_page_issue = True
    has_next_page_release = True
    has_next_page_discussion = True
    page_num = 0

    items = []
    while (
        has_next_page_pr
        or has_next_page_issue
        or has_next_page_release
        or has_next_page_discussion
    ):
        result = execute_query(query, variables, use_cache=use_cache)
        page_num += 1
        repo_data = result["repository"]

        if has_next_page_pr:
            prs = repo_data["pullRequests"]
            prs_per_page = 0
            for pr in prs["nodes"]:
                if parse_date(pr["updatedAt"]) < start_date:
                    has_next_page_pr = False
                    break
                items.append(
                    {
                        **pr,
                        "comments": pr["comments"]["nodes"],
                        "commits": pr["commits"]["nodes"],
                        "type": "pull_request",
                    },
                )
                prs_per_page += 1
            has_next_page_pr &= prs["pageInfo"]["hasNextPage"]
            variables["afterPR"] = prs["pageInfo"]["endCursor"]
            logging.info("Page %d: %d PRs", page_num, prs_per_page)

        if has_next_page_issue:
            issues = repo_data["issues"]
            issues_per_page = 0
            for issue in issues["nodes"]:
                if parse_date(issue["updatedAt"]) < start_date:
                    has_next_page_issue = False
                    break
                items.append(
                    {
                        **issue,
                        "comments": issue["comments"]["nodes"],
                        "type": "issue",
                    },
                )
                issues_per_page += 1
            has_next_page_issue &= issues["pageInfo"]["hasNextPage"]
            variables["afterIssue"] = issues["pageInfo"]["endCursor"]
            logging.info("Page %d: %d issues", page_num, issues_per_page)

        if has_next_page_release:
            releases = repo_data["releases"]
            releases_per_page = 0
            for release in releases["nodes"]:
                if parse_date(release["createdAt"]) < start_date:
                    has_next_page_release = False
                    break
                items.append(
                    {
                        **release,
                        "updatedAt": release["createdAt"],
                        "type": "release",
                    },
                )
                releases_per_page += 1
            has_next_page_release &= releases["pageInfo"]["hasNextPage"]
            variables["afterRelease"] = releases["pageInfo"]["endCursor"]
            logging.info("Page %d: %d releases", page_num, releases_per_page)

        if has_next_page_discussion:
            discussions = repo_data["discussions"]
            discussions_per_page = 0
            for discussion in discussions["nodes"]:
                if parse_date(discussion["updatedAt"]) < start_date:
                    has_next_page_discussion = False
                    break
                if not get_summary_discussion_metadata(discussion):
                    items.append(
                        {
                            **discussion,
                            "comments": discussion["comments"]["nodes"],
                            "type": "discussion",
                        },
                    )
                    discussions_per_page += 1
            has_next_page_discussion &= discussions["pageInfo"]["hasNextPage"]
            variables["afterDiscussion"] = discussions["pageInfo"]["endCursor"]
            logging.info("Page %d: %d discussions", page_num, discussions_per_page)

    return sorted(items, key=lambda x: x["updatedAt"], reverse=True)


def get_summary_discussion_metadata(
    discussion: dict[str, Any],
) -> dict[str, Any] | None:
    """Extract metadata from a summary discussion if it exists."""
    body = discussion["body"].replace("\r\n", "\n")
    match = re.search(r"```json\n(.*?)\n```", body, re.DOTALL)
    if match:
        try:
            metadata = json.loads(match.group(1))
            if (
                "powered_by" in metadata
                and "repo-summary-post" in metadata["powered_by"]
                and "end_date" in metadata
            ):
                return metadata
        except json.JSONDecodeError:
            pass
    return None


@measure_time
def summarize_prs_issues_releases_and_discussions(
    repo_owner: str,
    repo_name: str,
    start_date: datetime,
    end_date: datetime,
    use_cache: bool = False,
) -> list[dict[str, Any]]:
    """Summarize PRs, Issues, Releases, and Discussions in date range using GraphQL."""
    summary = []
    context = ActivityContext(repo_owner, repo_name, start_date, end_date)

    for item in fetch_pull_requests_issues_releases_and_discussions(
        repo_owner,
        repo_name,
        start_date,
        use_cache=use_cache,
    ):
        if should_include_item(item, start_date, end_date):
            if item["type"] == "pull_request":
                summary.append(process_pr(context, item))
            elif item["type"] == "issue":
                summary.append(process_issue(context, item))
            elif item["type"] == "release":
                summary.append(process_release(item))
            else:  # discussion
                summary.append(process_discussion(context, item))

    logging.info(
        "On %s..%s, found"
        " %d PRs/issues/releases/discussions,"
        " %d comments and"
        " %d commits",
        start_date.date(),
        end_date.date(),
        len(summary),
        count_comments(summary),
        count_commits(summary),
    )
    return summary


def count_comments(summary: list[dict[str, Any]]) -> int:
    """Count the number of comments in a summary."""
    return sum(
        1
        for item in summary
        for activity in item.get("recent_activities", [])
        if activity["type"] == "comment"
    )


def count_commits(summary: list[dict[str, Any]]) -> int:
    """Count the number of commits in a summary."""
    return sum(
        1
        for item in summary
        if item["type"] == "pull_request"
        for activity in item["recent_activities"]
        if activity["type"] == "commit"
    )


def process_discussion(
    context: ActivityContext,
    discussion: dict[str, Any],
) -> dict[str, Any]:
    """Process a single discussion and return its summary."""
    return {
        "number": discussion["number"],
        "created_at": discussion["createdAt"],
        "updated_at": discussion["updatedAt"],
        "title": discussion["title"],
        "category": discussion["category"]["name"],
        "body": discussion.get("body"),
        "recent_activities": process_activities(context, discussion),
        "type": "discussion",
    }


def should_include_item(
    item: dict[str, Any],
    start_date: datetime,
    end_date: datetime,
) -> bool:
    """Determine if an item should be included in the summary."""
    created_at = parse_date(item["createdAt"])

    if item["type"] == "release":
        logging.debug("Found release: %s on %s", item["name"], created_at.date())
        return start_date <= created_at < end_date

    if end_date <= created_at:
        return False  # created only after the period, skip

    if start_date <= parse_date(item["updatedAt"]) < end_date:
        return True  # at least some activity within the period, include

    if start_date <= parse_date(item["createdAt"]) < end_date:
        return True  # created within the period but no other activity yet, include

    closed_at = item.get("closedAt") and parse_date(item["closedAt"])
    if closed_at:
        if closed_at < start_date:
            return False  # closed before period, skip
        elif closed_at < end_date:
            return True  # closed within period, include

    if item["type"] == "pull_request":
        merged_at = item.get("mergedAt") and parse_date(item["mergedAt"])
        if merged_at:
            if merged_at < start_date:
                return False  # merged before period, skip
            elif merged_at < end_date:
                return True  # merged within period, include

    for comment in item.get("comments", []):
        if start_date <= parse_date(comment["createdAt"]) < end_date:
            return True  # at least one comment within period, include

    if item["type"] == "pull_request":
        for commit in item.get("commits", []):
            if start_date <= parse_date(commit["commit"]["committedDate"]) < end_date:
                return True  # at least one commit within period, include

    return False  # no comments or commits within period, skip


def process_pr(
    context: ActivityContext,
    pr: dict[str, Any],
) -> dict[str, Any]:
    """Process a single pull request and return its summary."""
    status = "merged" if pr["merged"] else pr["state"].lower()

    return {
        "number": pr["number"],
        "created_at": pr["createdAt"],
        "updated_at": pr["updatedAt"],
        "title": pr["title"],
        "status": status,
        "body": pr.get("body"),
        "recent_activities": process_activities(context, pr),
        "type": "pull_request",
    }


def process_activities(
    context: ActivityContext,
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    """Process all activities for a PR or issue and return activities within period."""
    activities = []

    # Process comments
    for comment in item["comments"]:
        activity_date = parse_date(comment["createdAt"])
        if context.start_date <= activity_date < context.end_date:
            activities.append(
                {
                    "type": "comment",
                    "date": activity_date,
                    "message": comment["body"].strip(),
                    "author": comment["author"]["login"],
                },
            )

    # Process commits (only for pull requests)
    if item["type"] == "pull_request":
        for commit in item["commits"]:
            commit_date = parse_date(commit["commit"]["committedDate"])
            if context.start_date <= commit_date < context.end_date:
                activities.append(
                    {
                        "type": "commit",
                        "date": commit_date,
                        "message": commit["commit"]["message"].strip(),
                        "author": commit["commit"]["author"]["name"],
                    },
                )

    # Process PR merge or close, or issue close
    if item["type"] == "pull_request" and item["mergedAt"]:
        merged_date = parse_date(item["mergedAt"])
        if context.start_date <= merged_date < context.end_date:
            activities.append(
                {
                    "type": "merge",
                    "date": merged_date,
                },
            )
    elif item["closedAt"]:
        closed_date = parse_date(item["closedAt"])
        if context.start_date <= closed_date < context.end_date:
            activities.append(
                {
                    "type": "close",
                    "date": closed_date,
                },
            )

    # Sort activities by date
    activities.sort(key=lambda x: x["date"])

    return activities


@measure_time
def create_discussion(repo: Repository, title: str, body: str, category: str) -> None:
    """Create a discussion in the repository using GraphQL."""
    try:
        category_id = get_or_create_category_id(repo, category)

        # Fetch the repository ID
        repo_query = gql(
            """
            query ($owner: String!, $name: String!) {
              repository(owner: $owner, name: $name) {
                id
              }
            }
            """,
        )
        repo_variables = {
            "owner": repo.owner.login,
            "name": repo.name,
        }
        repo_result = execute_query(repo_query, repo_variables)
        repo_id = repo_result["repository"]["id"]

        create_discussion_mutation = gql(
            """
            mutation CreateDiscussion($input: CreateDiscussionInput!) {
              createDiscussion(input: $input) {
                discussion {
                  id
                  url
                }
              }
            }
            """,
        )

        variables = {
            "input": {
                "repositoryId": repo_id,
                "categoryId": category_id,
                "title": title,
                "body": body,
            },
        }

        result = execute_query(create_discussion_mutation, variables)
        discussion_url = result["createDiscussion"]["discussion"]["url"]
        actions.core.info(f"Discussion created successfully: {discussion_url}")
        actions.core.info(f'Title: "{title}"')
        actions.core.info(f"First lines of body: {body.splitlines()[:3]}")

    except Exception as e:
        actions.core.error(f"Error creating discussion: {e}")
        raise


def get_category_id(repo: Repository, category_name: str) -> str | None:
    """Get the ID of a discussion category based on its name."""
    query = gql(
        """
        query ($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            discussionCategories(first: 100) {
              nodes {
                id
                name
              }
            }
          }
        }
        """,
    )

    variables = {
        "owner": repo.owner.login,
        "name": repo.name,
    }

    try:
        result = cached_execute(query, variables)
        categories = result["repository"]["discussionCategories"]["nodes"]
        for cat in categories:
            if cat["name"].lower() == category_name.lower():
                return cat["id"]
        return None
    except Exception as e:
        actions.core.error(f"Error fetching category ID: {e}")
        return None


def find_newest_summaries(
    repo: Repository,
    category: str,
    count: int = 3,
    use_cache: bool = False,
) -> list[tuple[date, str, str]]:
    """Find the newest previous summaries from the given discussion category."""
    category_id = get_category_id(repo, category)

    query = gql(
        """
        query ($owner: String!, $name: String!, $categoryId: ID!, $count: Int!) {
          repository(owner: $owner, name: $name) {
            discussions(first: $count,
                        categoryId: $categoryId,
                        orderBy: {field: UPDATED_AT, direction: DESC}) {
              nodes {
                title
                body
                createdAt
                updatedAt
              }
            }
          }
        }
        """,
    )

    variables = {
        "owner": repo.owner.login,
        "name": repo.name,
        "categoryId": category_id,
        "count": count,
    }

    try:
        result = execute_query(query, variables, use_cache=use_cache)
        discussions = result["repository"]["discussions"]["nodes"]

        summaries = []
        for discussion in discussions:
            metadata = get_summary_discussion_metadata(discussion)
            if metadata:
                end_date = datetime.strptime(metadata["end_date"], "%Y-%m-%d").date()
                summaries.append(
                    (
                        end_date,  # this is the UI end date
                        discussion["title"],
                        discussion["body"].replace("\r\n", "\n"),
                    ),
                )

        return sorted(summaries, reverse=True)[:count]
    except TransportQueryError as e:
        if "Could not resolve to a Repository with the name" in str(e):
            actions.core.warning(f"Repository or category not found: {e}")
        else:
            actions.core.error(f"GraphQL query error: {e}")
        raise
    except Exception as e:
        actions.core.error(f"Unexpected error finding newest summaries: {e}")
        raise


def process_issue(
    context: ActivityContext,
    issue: dict[str, Any],
) -> dict[str, Any]:
    """Process a single issue and return its summary."""
    status = issue["state"].lower()

    return {
        "number": issue["number"],
        "created_at": issue["updatedAt"],
        "updated_at": issue["updatedAt"],
        "title": issue["title"],
        "status": status,
        "body": issue.get("body"),
        "recent_activities": process_activities(context, issue),
        "type": "issue",
    }


def get_or_create_category_id(repo: Repository, category_name: str) -> str:
    """Get the ID of a discussion category or create it if it doesn't exist."""
    category_id = get_category_id(repo, category_name)
    if category_id:
        return category_id

    create_category_mutation = gql(
        """
        mutation CreateDiscussionCategory($input: CreateDiscussionCategoryInput!) {
          createDiscussionCategory(input: $input) {
            category {
              id
            }
          }
        }
        """,
    )

    variables = {
        "input": {
            "repositoryId": repo.id,
            "name": category_name,
            "description": f"Category for {category_name}",
            "emoji": ":speech_balloon:",
        },
    }

    try:
        result = execute_query(create_category_mutation, variables)
        return result["createDiscussionCategory"]["category"]["id"]
    except Exception as e:
        actions.core.error(f"Error creating discussion category: {e}")
        raise


def process_release(release: dict[str, Any]) -> dict[str, Any]:
    """Process a single release and return its summary."""
    return {
        "name": release["name"],
        "tag_name": release["tagName"],
        "created_at": release["createdAt"],
        "body": release.get("description"),
        "url": release["url"],
        "type": "release",
    }
