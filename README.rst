Repo Summary Post
=================

A GitHub Action to generate summaries of repository activity
using a large language model.

Usage
-----

To use this action in your GitHub workflow,
add the following step to your ``.github/workflows/weekly-summary.yml`` file:

.. code-block:: yaml

    name: Weekly Repository Summary
    on:
      schedule:
        - cron: '0 0 * * 1'  # Run every Monday at 00:00 UTC
      workflow_dispatch:  # Allow manual triggering

    jobs:
      create-summary:
        runs-on: ubuntu-latest
        steps:
          - name: Generate and Post Repository Summary
            uses: akaihola/repo-summary-post@v0.0.4
            with:
              github-token: ${{ secrets.GITHUB_TOKEN }}
              repo-name: ${{ github.repository }}

This action will generate a summary of the repository's activity
and post it as a new discussion in the specified category.
The summary period is automatically determined
based on previous summaries or repository creation date,
and extends until there's sufficient activity to summarize.

For more detailed information and configuration options,
please visit the `GitHub repository <https://github.com/akaihola/repo-summary-post>`_.

Input option reference
----------------------

This is the complete set of input options for the action, with their default values:

```yaml
          - name: Generate and Post Repository Summary
            uses: akaihola/repo-summary-post@v0.0.4
            with:
              github-token: ${{ secrets.GITHUB_TOKEN }}
              repo-name: ${{ github.repository }}
              category: 'Announcements'             # Discussions category to post in
              project-name: 'My Awesome Project'    # to override the repository name
              model: 'openrouter/anthropic/claude-3.5-sonnet:beta'  # Default model

              # Optional inputs for debugging purposes:
              verbose: '0'        # Default: 0 (no verbose output)
              dry-run: 'false'    # Default: false
              start: ''           # YYYY-MM-DD start date for the summary
              output-content: ''  # Path to render the GitHub activity report for debugging
              output: ''          # Path to output the AI summary for debugging
              output-prompt: ''   # Path to output the LLM prompt for debugging
```