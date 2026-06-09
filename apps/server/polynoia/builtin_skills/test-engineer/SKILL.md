---
name: test-engineer
description: Create focused test plans, automated tests, fixtures, and regression coverage.
---

# Test Engineer

Use this skill for test design, bug reproduction, regression suites, and quality gates.

Guidelines:
- Start from the behavior contract and failure modes.
- Add the narrowest tests that would fail before the fix and pass after it.
- Include edge cases for permissions, empty data, concurrency, invalid input, and persistence.
- Prefer deterministic tests over sleeps and network dependencies.
- Run the relevant test target and report what passed or why it could not run.
