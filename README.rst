=====================================================================
 repo-summary-post – AI generated weekly repository activity reports
=====================================================================

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
           uses: akaihola/repo-summary-post@v0.0.8
           with:
             github-token: ${{ secrets.GITHUB_TOKEN }}
             repo-name: ${{ github.repository }}
           env:
             OPENROUTER_KEY: ${{ secrets.OPENROUTER_KEY }}

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

.. code-block:: yaml

   - name: Generate and Post Repository Summary
     uses: akaihola/repo-summary-post@v0.0.8
     env:
       OPENROUTER_KEY: ${{ secrets.OPENROUTER_KEY }}
     with:
       github-token: ${{ secrets.GITHUB_TOKEN }}
       repo-name: ${{ github.repository }}
       category: 'Announcements'             # Discussions category to post in
       project-name: 'My Awesome Project'    # to override the repository name
       model: 'openrouter/anthropic/claude-3.5-sonnet:beta'  # Default model
       extra-packages: 'llm-openrouter'  # Required for the default model

       # Optional inputs for debugging purposes:
       verbose: '0'        # Default: 0 (no verbose output)
       dry-run: 'false'    # Default: false
       start: ''           # YYYY-MM-DD start date for the summary
       output-content: ''  # Path to render the GitHub activity report for debugging
       output: ''          # Path to output the AI summary for debugging
       output-prompt: ''   # Path to output the LLM prompt for debugging

Note: The default model requires the `llm-openrouter` package. For other LLM providers,
you need to install the corresponding `llm` plugin (e.g., 'llm-gemini' for Google's Gemini models)
by specifying it in the `extra-packages` input.

Configuring LLM API Keys
------------------------

To use the LLM for generating summaries, you need to provide the appropriate API key.
Here's how to configure the API key for different providers:

1. OpenRouter (default provider):

   Add the OpenRouter API key to your GitHub repository secrets and include it in your workflow:

   .. code-block:: yaml

      - name: Generate and Post Repository Summary
        uses: akaihola/repo-summary-post@v0.0.8
        env:
          OPENROUTER_KEY: ${{ secrets.OPENROUTER_KEY }}
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          repo-name: ${{ github.repository }}
          model: 'openrouter/anthropic/claude-3.5-sonnet:beta'  # This is the default model
          extra-packages: 'llm-openrouter'  # Required for OpenRouter models

2. Anthropic:

   If you want to use Anthropic's Claude model directly,
   add the Anthropic API key to your secrets and update the workflow:

   .. code-block:: yaml

      - name: Generate and Post Repository Summary
        uses: akaihola/repo-summary-post@v0.0.8
        env:
          ANTHROPIC_KEY: ${{ secrets.ANTHROPIC_KEY }}
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          repo-name: ${{ github.repository }}
          model: 'anthropic/claude-3-sonnet-20240229'

3. OpenAI:

   To use OpenAI models, add the OpenAI API key to your secrets and update the workflow:

   .. code-block:: yaml

      - name: Generate and Post Repository Summary
        uses: akaihola/repo-summary-post@v0.0.8
        env:
          OPENAI_KEY: ${{ secrets.OPENAI_KEY }}
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          repo-name: ${{ github.repository }}
          model: 'openai/gpt-4-turbo-preview'

Make sure to keep your API keys secure by using GitHub secrets and never exposing them in your repository code or logs.
