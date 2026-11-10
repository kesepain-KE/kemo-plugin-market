"""允许 ``python -m kemo_plugin_market`` 直接运行。"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
