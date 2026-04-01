# Working Philosophy — AI Director Framework

## Core Principle
Work backwards from a successful deployment before writing a single line of code.
If you cannot describe the exact path from local file to live running system without
ambiguity, stop and research first.

## Pre-Build Checklist (complete before any code)
1. What is the exact end state? (one sentence)
2. What is the deployment path? (every step from local to live)
3. What are the top 3 failure points? (search for known issues)
4. What files will be created or modified? (list explicitly)
5. What credentials or environment variables are needed? (confirm they exist)
6. Is there a simpler path? (if yes, take it)

## Architecture Pattern
- Each system is independent — if one breaks, others keep running
- Systems communicate through APIs, not direct dependencies
- Memory accumulates over time via shared state files
- Every project follows the same scaffold

## Project Scaffold (every project gets these)
project-name/
├── CLAUDE.md          # Instructions for Claude Code (under 60 lines)
├── architecture.md    # System design and data flow
├── state.md           # Current status, what's done, what's next
├── learnings/         # Mistakes made and rules learned
├── .gitignore         # Never commit secrets
└── [project files]

## Tech Stack (default starting point)
- Financial models / data tools: Python + SQLite
- Web dashboards: FastAPI + React
- Deployment: Railway + GitHub
- Alerts: Telegram bot API
- AI: Claude Code (Sonnet 4.6)

## Hard Rules (never violate)
1. Never overwrite any file without reading its current contents first
2. Never touch .env without stating what will change and why
3. Never commit .env or secrets to GitHub
4. Always search for known compatibility issues before building
5. Always add a /health endpoint to every web service
6. Always run tests after any change before declaring done
7. Never declare a task complete without verification — done means verified, not built
8. When uncertain, FLAG it rather than guess — wrong guesses cost more than paused work
9. Make the minimum change needed — surgical edits, not rewrites
10. Read WORKING_PHILOSOPHY.md at the start of every session

## Self-Improvement Loop (run after every deployment)
1. What broke?
2. Why did it break? (root cause, not symptoms)
3. What rule would have prevented it?
4. Add that rule to Hard Rules above
