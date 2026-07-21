# Contributing to Polynoia

Thank you for contributing to Polynoia. By participating, you agree to follow
our [Code of Conduct](CODE_OF_CONDUCT.md). Report security concerns through the
private process in our [Security Policy](SECURITY.md), not in a public issue.

## Prerequisites

Install these tools before setting up the repository:

- Git
- Make
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 or newer
- pnpm 9

## Set up the repository

From the repository root, install the backend and frontend dependencies:

```bash
make install
```

Start the backend and web development servers together:

```bash
make dev
```

The development API is not hardened for deployment and must not be exposed to
an untrusted network. Restrict access with an appropriate trusted network and
host firewall while developing.

## Validate changes

Run the checks relevant to your change before opening a pull request.

Focused backend tests:

```bash
cd apps/server && uv run pytest -q
```

Frontend tests:

```bash
pnpm --filter @polynoia/web test
```

Frontend type-check:

```bash
pnpm --filter @polynoia/web exec tsc --noEmit
```

Frontend production build:

```bash
pnpm --filter @polynoia/web build
```

## Propose and submit changes

- Open a [GitHub issue](https://github.com/JuneQQQ/polynoia/issues) before
  implementing a large change so its scope and approach can be discussed.
- Keep commits focused and use a conventional commit subject such as `feat:`,
  `fix:`, `docs:`, or `chore:`.
- Add or update tests for behavior changes and run the relevant checks above.
- Include screenshots only when a pull request makes a real user-interface
  change that reviewers need to see.
- Never commit API keys, access tokens, credentials, private data, or other
  secrets.
- Keep unrelated formatting or refactoring out of the pull request.

Git worktrees isolate branches and concurrent Git collaboration; they are not
an operating-system security sandbox. Agents that can run shell commands act
with the authority of the local user, so review their access and protect local
credentials and data accordingly.
