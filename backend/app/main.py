"""FastAPI application assembly root using modern lifespan management.

Nothing inside app/ imports app.main: all routes pull services from request.app.state.container.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import APP_VERSION
from app.api.routes import (
    auth,
    brains,
    channels,
    chat,
    experience,
    health,
    hub,
    learn,
    models,
    onboarding,
    sessions,
    settings,
    studio,
    terminal_webhooks,
    work,
)
from app.container import AppContainer
from app.security.middleware import SecurityMiddleware

load_dotenv()

SCHEDULER_INTERVAL_S = 20.0


async def _scheduler_loop(ctr: AppContainer) -> None:
    """Periodic 24/7 background tick for Kanban, Multi-Loop, and Reminders."""
    while True:
        await asyncio.sleep(SCHEDULER_INTERVAL_S)
        try:
            brain_names = [b.name for b in ctr.vaults.list_brains()]
            if not brain_names:
                continue
            await ctr.reminders.tick(brain_names)
            await ctr.loops.tick(brain_names)
            await ctr.kanban.tick(brain_names)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            continue


def create_app(container: AppContainer | None = None, *, enable_scheduler: bool = True) -> FastAPI:
    ctr = container or AppContainer.build()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.container = ctr
        sched_task = asyncio.create_task(_scheduler_loop(ctr)) if enable_scheduler else None
        watcher_task = asyncio.create_task(ctr.watcher.start()) if enable_scheduler else None
        if enable_scheduler and ctr.telegram.is_enabled():
            ctr.telegram.start()
        try:
            yield
        finally:
            if watcher_task is not None:
                await ctr.watcher.stop()
            if sched_task is not None:
                sched_task.cancel()
                await asyncio.gather(sched_task, return_exceptions=True)
            await ctr.telegram.stop()
            await ctr.chat_runtime.shutdown()

    application = FastAPI(
        title="Jarvis OS",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    application.state.container = ctr
    application.add_middleware(
        SecurityMiddleware,
        auth=ctr.auth,
        allowed_hosts=ctr.allowed_hosts,
    )

    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(settings.router)
    application.include_router(onboarding.router)
    application.include_router(brains.router)
    application.include_router(sessions.router)
    application.include_router(models.router)
    application.include_router(chat.router)
    application.include_router(hub.router)
    application.include_router(studio.router)
    application.include_router(work.router)
    application.include_router(learn.router)
    application.include_router(experience.router)
    application.include_router(channels.router)
    application.include_router(terminal_webhooks.router)

    @application.get("/robots.txt", response_class=PlainTextResponse)
    def robots_txt() -> str:
        return "User-agent: *\nDisallow: /\n"

    dist_dir = ctr.paths.dashboard_dist_dir
    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        application.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @application.get("/", include_in_schema=False)
    def serve_index():
        index_file = dist_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return PlainTextResponse(
            "Jarvis OS API is running. Start the Vite dev server in dashboard/ or run `npm run build`.",
            status_code=200,
        )

    return application


app = create_app()
