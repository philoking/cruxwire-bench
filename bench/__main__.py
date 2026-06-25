"""Run the console: `python -m bench`."""

from __future__ import annotations

import uvicorn

from . import config


def main() -> None:
    config.ensure_dirs()
    uvicorn.run("bench.web.app:app", host=config.HOST, port=config.PORT, reload=False)


if __name__ == "__main__":
    main()
