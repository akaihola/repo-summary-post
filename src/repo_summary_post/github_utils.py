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
from cachetools import LRUCache, cached, keys
from gql import Client, gql
from gql.transport.exceptions import TransportQueryError
from gql.transport.requests import RequestsHTTPTransport

from repo_summary_post.caching import cached_execute

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from github.Repository import Repository

T = TypeVar("T")


def parse_date(date_str: str) -> datetime:
    """Parse a date string in ISO format."""
    return datetime.fromisoformat(date_str.rstrip("Z")).replace(tzinfo=UTC)


# Create an LRU cache with a maximum size of 100 items
query_cache = LRUCache(maxsize=100)


def cache_key(query, variables, **kwargs):
    """Create a cache key from the function arguments."""
    return keys.hashkey(query.loc.source.body, json.dumps(variables, sort_keys=True))


@cached(query_cache, key=cache_key)  # in-memory cache always enabled
def execute_query(query, variables, use_cache=False):
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
    page_num = 0

    while has_next_page_pr or has_next_page_issue:
        result = execute_query(query, variables, use_cache=use_cache)
        page_num += 1
        repo_data = result["repository"]

        if has_next_page_pr:
            prs = repo_data["pullRequests"]
            for pr in prs["nodes"]:
                pr_copy = pr.copy()
                pr_copy["comments"] = pr["comments"]["nodes"]
                pr_copy["commits"] = pr["commits"]["nodes"]
                pr_copy["type"] = "pull_request"
                yield pr_copy
            has_next_page_pr = prs["pageInfo"]["hasNextPage"]
            variables["afterPR"] = prs["pageInfo"]["endCursor"]
            logging.info("Page %d: %d PRs", page_num, len(prs["nodes"]))

        if has_next_page_issue:
            issues = repo_data["issues"]
            for issue in issues["nodes"]:
                issue_copy = issue.copy()
                issue_copy["comments"] = issue["comments"]["nodes"]
                issue_copy["type"] = "issue"
                yield issue_copy
            has_next_page_issue = issues["pageInfo"]["hasNextPage"]
            variables["afterIssue"] = issues["pageInfo"]["endCursor"]
            logging.info("Page %d: %d issues", page_num, len(issues["nodes"]))


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
        if should_include_item(item, start_date, end_date):
            if item["type"] == "pull_request":
                summary.append(process_pr(context, item))
            else:
                summary.append(process_issue(context, item))
        elif parse_date(item["updatedAt"]) < start_date:
            break

    logging.info(
        "During %s–%s, found %d PRs and issues, %d comments and %d commits",
        start_date.date(),
        end_date.date(),
        len(summary),
        sum(
            1
            for item in summary
            for activity in item["recent_activities"]
            if activity["type"] == "comment"
        ),
        sum(
            1
            for item in summary
            if item["type"] == "pull_request"
            for activity in item["recent_activities"]
            if activity["type"] == "commit"
        ),
    )
    return summary


def should_include_item(
    item: dict[str, Any], start_date: datetime, end_date: datetime
) -> bool:
    """Determine if an item should be included in the summary."""
    if end_date <= parse_date(item["createdAt"]) < end_date:
        return False  # created only after the period, skip

    if start_date <= parse_date(item["updatedAt"]) < end_date:
        return True  # at least some activity within the period, include

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

    for comment in item["comments"]:
        if start_date <= parse_date(comment["createdAt"]) < end_date:
            return True  # at least one comment within period, include

    if item["type"] == "pull_request":
        for commit in item["commits"]:
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
        activity_data = {
            "type": "comment",
            "date": activity_date,
            "message": comment["body"].strip(),
            "author": comment["author"]["login"],
        }
        if context.start_date <= activity_date < context.end_date:
            activities.append(activity_data)

    # Process commits (only for pull requests)
    if item["type"] == "pull_request":
        for commit in item["commits"]:
            commit_date = parse_date(commit["commit"]["committedDate"])
            activity_data = {
                "type": "commit",
                "date": commit_date,
                "message": commit["commit"]["message"].strip(),
                "author": commit["commit"]["author"]["name"],
            }
            if context.start_date <= commit_date < context.end_date:
                activities.append(activity_data)

    # Process PR merge or close, or issue close
    if item["type"] == "pull_request" and item["mergedAt"]:
        merged_date = parse_date(item["mergedAt"])
        activity_data = {
            "type": "merge",
            "date": merged_date,
        }
        if context.start_date <= merged_date < context.end_date:
            activities.append(activity_data)
    elif item["closedAt"]:
        closed_date = parse_date(item["closedAt"])
        activity_data = {
            "type": "close",
            "date": closed_date,
        }
        if context.start_date <= closed_date < context.end_date:
            activities.append(activity_data)

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
