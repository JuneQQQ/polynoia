"""Allow ``python -m polynoia.cli`` to launch the chat CLI."""
from __future__ import annotations

import sys

from polynoia.cli.chat import main

sys.exit(main())
