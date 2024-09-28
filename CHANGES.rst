Unreleased_
===========

These features will be included in the next release:

Added
-----
- Support the OpenAI API when ``OPENAI_KEY`` is defined.
- Add workflow for testing supported model APIs.
- Replace ``pip`` with ``uv`` to speed up the action.

Fixed
-----
- Detect model names by both a "provider/" and a "model-" prefix.


0.0.8_ - 2024-08-16
===================

Fixed
-----
- Fixed end timestamp filter in summary generation that was incorrectly including an extra day,
  by using ``datetime.min.time()`` instead of ``datetime.max.time()`` for the end date.


0.0.7_ - 2024-08-16
===================

Added
-----
- Always run using Python 3.12 version.
- Generate and return the LLM prompt separately from the summary.
- Make boolean arguments keyword-only for improved clarity.

Fixed
-----
- Filter out unrelated activity in GitHub summaries. Now looks at specific activity in the period,
  like merges and comments, instead of including items with ``updatedAt`` in the given period.
- Use ``dict[]`` for typing instead of ``Dict[]`` for better consistency with modern Python typing practices.
- Add missing return types to functions for improved type hinting.
- Split long lines to improve code readability.
- Use ``elif:`` instead of ``else:`` followed by ``if:`` for better readability.
- Add trailing commas to multi-line function calls and data structures for easier version control diffs.
- Ignore Any typing errors to reduce noise in type checking.
- Refactor summary generation into its own module for better code organization.
- Use naive date parsing to avoid timezone-related issues.
- Address various style issues flagged by Ruff, including unsafe fixes.

Removed
-------
- Drop unused dependencies, including llm-openrouter and requests-cache.


0.0.6_ - 2024-08-12
===================

Added
-----
- Skip summary creation if not enough activity is detected
- New ``have_enough_content()`` function to check for sufficient content before summarizing
- Support for OpenRouter and Claude API keys
- Automatic collection and setup of API keys and plugins based on selected model
- Extra commands input for running additional shell commands before summary script

Fixed
-----
- Correct usage of OpenRouter and Claude keys
- Improved model selection and API key handling
- Updated Python version requirement to >=3.10 (previously >=3.12)
- Refactored ``write_output()`` function to handle cases with no title


0.0.5_ - 2024-08-11
===================

Added
-----
- New ``get_config()`` function to handle configuration from both command line arguments and GitHub Action inputs
- Support for specifying the end date shown in UI separately from the actual end date used for queries

Fixed
-----
- Corrected handling of end dates in summary titles and metadata
- Improved argument parsing to combine command line arguments with GitHub Action inputs
- Updated how default values are handled for various configuration options

Removed
-------
- Eliminated direct use of environment variables for configuration
- Removed ``get_env_or_arg()`` function in favor of new ``get_config()`` function


0.0.4_ - 2024-08-11
===================

Added
-----
- Support for summarizing discussions in addition to PRs, issues and releases
- New ``extra-packages`` input option to specify additional Python packages to install
- Inclusion of previous summary titles when rendering past summaries
- Improved handling of discussions in the activity report template

Fixed
-----
- Corrected input names in action environment variables to use dashes instead of underscores
- Resolved issue with rendering discussion category in activity report
- Addressed typo in README
- Improved API key configuration instructions in README
- Turned off autoescaping for Jinja2 templates to prevent unwanted HTML escaping

Removed
-------
- Eliminated redundant summary discussion identification logic


0.0.3_ - 2024-08-11
===================

Added
-----
- Included releases in summary generation and output
- Added more instructions for LLM prompt to improve summary quality
- Implemented processing of GitHub releases

Fixed
-----
- Corrected issue where scanning of events was breaking too early
- Resolved problem with leading empty lines in AI summary template


0.0.2_ - 2024-08-11
===================

Added
-----
- New ``--start`` command line argument to specify start date for summary
- In-memory caching using LRUCache to potentially speed up processing during silent periods
- More concise log message showing PR/issue, comment and commit counts
- Improved filtering and date handling for activities
- Human-formatted date range in LLM prompt

Fixed
-----
- Corrected handling of ``createdAt`` field for pull requests
- Improved date comparisons to use ``<`` instead of ``<=`` for end dates
- Ensured correct year is used in LLM-generated summaries
- Removed quotes from title format in LLM prompt
- Excluded metadata from previous summaries in LLM prompt
- Corrected typo with ``createdAt`` field
- Improved indentation of body/comment text in activity report template
- Ensured first line of LLM response is the title

Removed
-------
- Eliminated old activities from input given to LLM


0.0.1_ - 2024-08-11
===================

Added
-----
- GitHub API integration to fetch pull requests, issues, releases, and discussions.
- GraphQL queries with caching mechanism for improved performance.
- Pagination handling for fetching large amounts of data from GitHub.
- Date range filtering for relevant activities.
- Templating system using Jinja2 for generating activity reports and summaries.
- Integrated LLM (Language Model) capabilities for generating AI summaries.
- Templates for generating LLM prompts and formatting AI summaries.
- Command-line interface with various options for customization.
- Configuration options for specifying project name, repository, and discussion category.
- Support for dry-run mode to preview summaries without posting.
- Support for creating GitHub discussions with generated summaries.
- Ability to find and reference previous summary discussions.
- Logging system with configurable verbosity levels.
- Error handling and reporting using GitHub Actions Core library.
- Utility functions for measuring execution time of key operations.


.. _Unreleased: https://github.com/akaihola/repo-summary-post/compare/v0.0.8...HEAD
.. _0.0.8: https://github.com/akaihola/repo-summary-post/compare/v0.0.7...v0.0.8
.. _0.0.7: https://github.com/akaihola/repo-summary-post/compare/v0.0.6...v0.0.7
.. _0.0.6: https://github.com/akaihola/repo-summary-post/compare/v0.0.5...v0.0.6
.. _0.0.5: https://github.com/akaihola/repo-summary-post/compare/v0.0.4...v0.0.5
.. _0.0.4: https://github.com/akaihola/repo-summary-post/compare/v0.0.3...v0.0.4
.. _0.0.3: https://github.com/akaihola/repo-summary-post/compare/v0.0.2...v0.0.3
.. _0.0.2: https://github.com/akaihola/repo-summary-post/compare/v0.0.1...v0.0.2
.. _0.0.1: https://github.com/akaihola/repo-summary-post/compare/9c575a0d...v0.0.1
