"""Utility functions for interacting with GitHub API."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, Optional, TypeVar

import actions.core
from github import BadCredentialsException, GithubException, UnknownObjectException
from gql import gql

from repo_summary_post.caching import cached_execute

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from github.Repository import Repository

T = TypeVar("T")


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
class PRContext:
    """Context object for processing Pull Requests."""

    repo_owner: str
    repo_name: str
    start_date: datetime
    end_date: datetime


def fetch_pull_requests(
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
        result = cached_execute(query, variables)
        prs = result["repository"]["pullRequests"]
        for pr in prs["nodes"]:
            pr["comments"] = pr["comments"]["nodes"]
            yield pr
        has_next_page = prs["pageInfo"]["hasNextPage"]
        variables["after"] = prs["pageInfo"]["endCursor"]


@measure_time
def summarize_prs(
    repo_owner: str,
    repo_name: str,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Summarize Pull Requests within a given date range using GraphQL."""
    summary = []
    context = PRContext(repo_owner, repo_name, start_date, end_date)

    for pr in fetch_pull_requests(repo_owner, repo_name):
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

    old_activities, recent_activities = process_activities(context, pr)

    return {
        "number": pr["number"],
        "updated_at": pr["updatedAt"],
        "title": pr["title"],
        "status": status,
        "body": pr.get("body"),
        "old_activities": old_activities,
        "recent_activities": recent_activities,
    }


def process_activities(
    context: PRContext,
    pr: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Process all activities for a pull request and return old and recent activities."""
    old_activities = []
    recent_activities = []

    # Process comments
    for comment in pr["comments"]:
        activity_date = datetime.fromisoformat(
            comment["createdAt"].rstrip("Z")
        ).replace(
            tzinfo=UTC,
        )
        activity_data = {
            "type": "comment",
            "date": activity_date,
            "body": comment["body"].strip(),
            "author": comment["author"]["login"],
        }
        if activity_date < context.start_date:
            old_activities.append(activity_data)
        elif context.start_date <= activity_date <= context.end_date:
            recent_activities.append(activity_data)

    # Process commits
    for commit in pr["commits"]["nodes"]:
        commit_date = datetime.fromisoformat(
            commit["commit"]["committedDate"].rstrip("Z")
        ).replace(
            tzinfo=UTC,
        )
        activity_data = {
            "type": "commit",
            "date": commit_date,
            "message": commit["commit"]["message"].strip(),
            "author": commit["commit"]["author"]["name"],
        }
        if commit_date < context.start_date:
            old_activities.append(activity_data)
        elif context.start_date <= commit_date <= context.end_date:
            recent_activities.append(activity_data)

    # Process PR merge or close
    if pr["mergedAt"]:
        merged_date = datetime.fromisoformat(pr["mergedAt"].rstrip("Z")).replace(
            tzinfo=UTC
        )
        activity_data = {
            "type": "merge",
            "date": merged_date,
        }
        if context.start_date <= merged_date <= context.end_date:
            recent_activities.append(activity_data)
    elif pr["closedAt"]:
        closed_date = datetime.fromisoformat(pr["closedAt"].rstrip("Z")).replace(
            tzinfo=UTC
        )
        activity_data = {
            "type": "close",
            "date": closed_date,
        }
        if context.start_date <= closed_date <= context.end_date:
            recent_activities.append(activity_data)

    # Sort activities by date
    old_activities.sort(key=lambda x: x["date"])
    recent_activities.sort(key=lambda x: x["date"])

    return old_activities, recent_activities


@measure_time
def create_discussion(repo: Repository, title: str, body: str, category: str) -> None:
    """Create a discussion in the repository."""
    try:
        # Use the correct method to create a discussion
        repo.create_discussion_category(category)  # type: ignore[attr-defined]
        repo.create_discussion_using_category(title=title, body=body, category=category)  # type: ignore[attr-defined]
        actions.core.info("Discussion created successfully.")
    except (GithubException, BadCredentialsException) as e:
        actions.core.error(f"Error creating discussion: {e!s}")
    except AttributeError:
        actions.core.error(
            "Error: The method to create a discussion is not available. "
            "Make sure you're using the latest version of PyGithub.",
        )
    except Exception as e:
        actions.core.error(f"Unexpected error creating discussion: {e!s}")
        raise  # Re-raise the exception after logging


def find_newest_summaries(
    repo: Repository, category: str, count: int = 3
) -> list[tuple[datetime, str]]:
    """Find the newest previous summaries from the given discussion category."""
    summaries = []
    try:
        # Use the REST API to list discussions
        url = f"/repos/{repo.owner.login}/{repo.name}/discussions"
        discussions = repo._requester.requestJsonAndCheck(
            "GET", url, {"category": category}
        )[1]

        for discussion in discussions:
            body = discussion.get("body", "")
            match = re.search(r"```json\n(.*?)\n```", body, re.DOTALL)
            if match:
                try:
                    metadata = json.loads(match.group(1))
                    if (
                        "powered_by" in metadata
                        and "/repo-summary-post/" in metadata["powered_by"]
                        and "end_date" in metadata
                    ):
                        end_date = datetime.strptime(
                            metadata["end_date"], "%Y-%m-%d"
                        ).date()
                        summaries.append((end_date, body))
                        if len(summaries) == count:
                            break
                except json.JSONDecodeError:
                    continue
    except UnknownObjectException:
        actions.core.warning(f"Category '{category}' not found. Creating a new one.")
    except GithubException as e:
        if e.status == 404:
            actions.core.warning(
                f"Category '{category}' not found. Creating a new one."
            )
        else:
            actions.core.error(f"Error finding newest summaries: {e!s}")
            raise
    except Exception as e:
        actions.core.error(f"Error finding newest summaries: {e!s}")
        raise
    return sorted(summaries, reverse=True)[:count]
