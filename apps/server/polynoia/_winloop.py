"""Windows event-loop fix for subprocess-spawning under uvicorn.

uvicorn 0.47's loop factory (``uvicorn/loops/asyncio.py``) returns a
``SelectorEventLoop`` on Windows whenever it manages worker subprocesses
(``--reload`` or ``--workers`` → ``use_subprocess=True``). On Windows a
``SelectorEventLoop`` cannot spawn subprocesses: ``asyncio.create_subprocess_exec``
raises ``NotImplementedError``. That would break **every** adapter CLI spawn
(claude / opencode / codex) and the onboarding ``--version`` probe.

The fix is to force a ``ProactorEventLoop`` (which supports subprocesses). We do
it by overriding ``Config.get_loop_factory`` on the *instance* — that attribute
lives in the config's ``__dict__``, so it is pickled to the reloader's spawned
worker child, where ``Server.run()`` calls ``config.get_loop_factory()``. A
module-level function (not a lambda) is required so it pickles cleanly.

Defined at module scope and platform-agnostic at import time (``ProactorEventLoop``
is only *accessed* when called, and we only call it on win32).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


def proactor_loop_factory() -> Callable[[], asyncio.AbstractEventLoop]:
    """Return the ProactorEventLoop class as uvicorn's loop factory.

    Assigned onto a ``uvicorn.Config`` instance as ``config.get_loop_factory``;
    uvicorn calls it and passes the result as ``loop_factory`` to ``asyncio.run``.
    """
    return asyncio.ProactorEventLoop  # type: ignore[attr-defined]  # win32-only
