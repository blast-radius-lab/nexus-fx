# Blast Radius Mentor CLI

A terminal-based chat interface for the Blast Radius learning platform.

## Prerequisites

Before installing the CLI, you need the lab project to work on:

1. **Fork the lab repo** — the main repo is read-only. Fork it to your own
   GitHub account so you can push your work.

   ```bash
   gh repo fork <org>/nexus-fx-lab --clone
   ```

   Or fork via the GitHub UI and clone your fork.

2. **Install the CLI** (see below).

3. **Run the CLI from inside your fork** — the mentor can read your local files
   and review your changes.

## Installation

```bash
pip install br-mentor
```

## Usage

```bash
# Authenticate (stores token locally at ~/.config/br-mentor/auth.json)
br-mentor auth login

# Start a chat session from your project directory
cd nexus-fx-lab
br-mentor chat

# Include specific files as context for the mentor to review
br-mentor chat --context ./services/api-gateway/Dockerfile --diff

# Send a single message (non-interactive)
br-mentor ask "What's wrong with this Dockerfile?" --context ./Dockerfile
```

## Features

- Rich markdown rendering in the terminal
- Streaming responses from the AI mentor
- File context attachment (local files and git diffs sent for review)
- Token persistence across sessions
- Phase-aware mentoring (resources, tasks, quizzes per section)

## How It Works

The CLI is a thin client. It authenticates you, collects your messages and
optional file context, and sends them to the mentor server. The server holds
the AI skill, system prompts, and API key — you never interact with those
directly. All your work happens locally in your fork.
