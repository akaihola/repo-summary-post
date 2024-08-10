"""Utility functions for interacting with GitHub API."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

import actions.core
from gql import Client, gql
from gql.transport.exceptions import TransportQueryError
from gql.transport.requests import RequestsHTTPTransport

from repo_summary_post.caching import cached_execute

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from github.Repository import Repository

T = TypeVar("T")


def execute_query(query, variables, use_cache=False):
    """Execute a GraphQL query."""
    if use_cache:

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


def fetch_pull_requests_and_issues(
    repo_owner: str,
    repo_name: str,
    use_cache: bool = False,
) -> Iterator[dict[str, Any]]:
    """Fetch Pull Requests, Issues, and comments using GraphQL, handling pagination."""
    query = gql(
        """
        query ($owner: String!, $name: String!, $afterPR: String, $afterIssue: String) {
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
          }
        }
        """,
    )

    variables: dict[str, Any] = {
        "owner": repo_owner,
        "name": repo_name,
        "afterPR": None,
        "afterIssue": None,
    }

    has_next_page_pr = True
    has_next_page_issue = True

    while has_next_page_pr or has_next_page_issue:
        result = execute_query(query, variables, use_cache=use_cache)
        repo_data = result["repository"]

        if has_next_page_pr:
            prs = repo_data["pullRequests"]
            for pr in prs["nodes"]:
                pr["comments"] = pr["comments"]["nodes"]
                pr["type"] = "pull_request"
                yield pr
            has_next_page_pr = prs["pageInfo"]["hasNextPage"]
            variables["afterPR"] = prs["pageInfo"]["endCursor"]

        if has_next_page_issue:
            issues = repo_data["issues"]
            for issue in issues["nodes"]:
                issue["comments"] = issue["comments"]["nodes"]
                issue["type"] = "issue"
                yield issue
            has_next_page_issue = issues["pageInfo"]["hasNextPage"]
            variables["afterIssue"] = issues["pageInfo"]["endCursor"]


@measure_time
def summarize_prs_and_issues(
    repo_owner: str,
    repo_name: str,
    start_date: datetime,
    end_date: datetime,
    use_cache: bool = False,
) -> list[dict[str, Any]]:
    """Summarize Pull Requests and Issues within a given date range using GraphQL."""
    summary = []
    context = ActivityContext(repo_owner, repo_name, start_date, end_date)

    for item in fetch_pull_requests_and_issues(
        repo_owner, repo_name, use_cache=use_cache
    ):
        item_updated_at = datetime.fromisoformat(item["updatedAt"].rstrip("Z")).replace(
            tzinfo=UTC,
        )

        if should_include_item(item, start_date, end_date):
            if item["type"] == "pull_request":
                summary.append(process_pr(context, item))
            else:
                summary.append(process_issue(context, item))
        elif item_updated_at < start_date:
            break

    return summary


def should_include_item(
    item: dict[str, Any], start_date: datetime, end_date: datetime
) -> bool:
    """Determine if an item should be included in the summary."""
    item_updated_at = datetime.fromisoformat(item["updatedAt"].rstrip("Z")).replace(
        tzinfo=UTC
    )

    if start_date <= item_updated_at <= end_date:
        return True

    if item["type"] == "pull_request":
        merged_at = item.get("mergedAt")
        if (
            merged_at
            and start_date
            <= datetime.fromisoformat(merged_at.rstrip("Z")).replace(tzinfo=UTC)
            <= end_date
        ):
            return True

    closed_at = item.get("closedAt")
    if (
        closed_at
        and start_date
        <= datetime.fromisoformat(closed_at.rstrip("Z")).replace(tzinfo=UTC)
        <= end_date
    ):
        return True

    for comment in item["comments"]:
        comment_created_at = datetime.fromisoformat(
            comment["createdAt"].rstrip("Z")
        ).replace(tzinfo=UTC)
        if start_date <= comment_created_at <= end_date:
            return True

    if item["type"] == "pull_request":
        for commit in item["commits"]["nodes"]:
            commit_date = datetime.fromisoformat(
                commit["commit"]["committedDate"].rstrip("Z")
            ).replace(tzinfo=UTC)
            if start_date <= commit_date <= end_date:
                return True

    return False


def process_pr(
    context: ActivityContext,
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
    context: ActivityContext,
    item: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Process all activities for a pull request or issue and return old and recent activities."""
    old_activities = []
    recent_activities = []

    # Process comments
    for comment in item["comments"]:
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

    # Process commits (only for pull requests)
    if item["type"] == "pull_request":
        for commit in item["commits"]["nodes"]:
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

    # Process PR merge or close, or issue close
    if item["type"] == "pull_request" and item["mergedAt"]:
        merged_date = datetime.fromisoformat(item["mergedAt"].rstrip("Z")).replace(
            tzinfo=UTC
        )
        activity_data = {
            "type": "merge",
            "date": merged_date,
        }
        if context.start_date <= merged_date <= context.end_date:
            recent_activities.append(activity_data)
    elif item["closedAt"]:
        closed_date = datetime.fromisoformat(item["closedAt"].rstrip("Z")).replace(
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
            """
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
            """
        )

        variables = {
            "input": {
                "repositoryId": repo_id,
                "categoryId": category_id,
                "title": title,
                "body": body,
            }
        }

        result = execute_query(create_discussion_mutation, variables)
        discussion_url = result["createDiscussion"]["discussion"]["url"]
        actions.core.info(f"Discussion created successfully: {discussion_url}")
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
        """
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
    repo: Repository, category: str, count: int = 3, use_cache: bool = False
) -> list[tuple[datetime, str]]:
    """Find the newest previous summaries from the given discussion category."""
    category_id = get_category_id(repo, category)

    query = gql(
        """
        query ($owner: String!, $name: String!, $categoryId: ID!, $count: Int!) {
          repository(owner: $owner, name: $name) {
            discussions(first: $count, categoryId: $categoryId, orderBy: {field: CREATED_AT, direction: DESC}) {
              nodes {
                body
                createdAt
              }
            }
          }
        }
        """
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
            # GitHub's editor replaces newlines with Windows ones:
            body = discussion["body"].replace("\r\n", "\n")
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
                except json.JSONDecodeError:
                    continue

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

    old_activities, recent_activities = process_activities(context, issue)

    return {
        "number": issue["number"],
        "updated_at": issue["updatedAt"],
        "title": issue["title"],
        "status": status,
        "body": issue.get("body"),
        "old_activities": old_activities,
        "recent_activities": recent_activities,
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
        """
    )

    variables = {
        "input": {
            "repositoryId": repo.id,
            "name": category_name,
            "description": f"Category for {category_name}",
            "emoji": ":speech_balloon:",
        }
    }

    try:
        result = execute_query(create_category_mutation, variables)
        return result["createDiscussionCategory"]["category"]["id"]
    except Exception as e:
        actions.core.error(f"Error creating discussion category: {e}")
        raise
