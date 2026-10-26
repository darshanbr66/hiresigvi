"""Local development entrypoint.

`uvicorn app.main:app` (invoked directly, or via `python -m uvicorn`) creates
its event loop with `asyncio.run()` *before* it imports the application
module — so setting the Windows event loop policy inside `app/main.py` is
too late; `asyncio.run()` has already picked up whatever policy was active
the moment it was called. Running through this script instead sets the
policy first, then hands off to uvicorn.

Not needed in production: this only matters on Windows, where the default
`ProactorEventLoop` is incompatible with psycopg3's async mode (see
`app/core/asyncio_compat.py`). On Linux/containers, `uvicorn app.main:app`
directly is fine.
"""

from app.core.asyncio_compat import configure_event_loop_policy

configure_event_loop_policy()

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000 or 8005, reload=True)
