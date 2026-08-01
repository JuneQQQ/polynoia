from __future__ import annotations

import importlib.metadata

A2A_SDK_VERSION = "1.1.2"
A2A_TCK_COMMIT = "5996b79f9cefa6fc390980e383e358a66fb9e49e"


def assert_supported_sdk() -> None:
    actual = importlib.metadata.version("a2a-sdk")
    if actual != A2A_SDK_VERSION:
        raise RuntimeError(
            f"polynoia-a2a-bridge requires a2a-sdk=={A2A_SDK_VERSION}; found {actual}"
        )
