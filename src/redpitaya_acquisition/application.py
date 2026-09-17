from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from redpitaya_acquisition.acquisition.manager import AcquisitionManager
from redpitaya_acquisition.acquisition.simulator import SimulatedAcquisitionSource
from redpitaya_acquisition.api.routes import router as api_router
from redpitaya_acquisition.api.websocket import router as websocket_router
from redpitaya_acquisition.config.settings import Settings, SourceKind
from redpitaya_acquisition.observability.logging import configure_logging
from redpitaya_acquisition.redpitaya.streaming_source import RedPitayaStreamingSource
from redpitaya_acquisition.streaming.broadcaster import Broadcaster


def create_app(settings: Settings | None = None) -> FastAPI:
    effective = settings or Settings()
    broadcaster = Broadcaster(effective.client_queue_capacity)

    def source_factory(current: Settings):  # type: ignore[no-untyped-def]
        if current.source is SourceKind.SIMULATOR:
            return SimulatedAcquisitionSource(current)
        return RedPitayaStreamingSource(current)

    manager = AcquisitionManager(effective, source_factory, broadcaster)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        app.state.manager = manager
        if effective.auto_start:
            await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(title="Red Pitaya Acquisition Service", version="0.1.0", lifespan=lifespan)
    app.state.manager = manager
    app.include_router(api_router)
    app.include_router(websocket_router)
    return app
