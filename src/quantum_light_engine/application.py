from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from quantum_light_engine.api.device_routes import router as device_router
from quantum_light_engine.api.engine_routes import router as engine_router
from quantum_light_engine.api.errors import install_error_handlers
from quantum_light_engine.api.websocket_routes import router as websocket_router
from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices.migration import migrate_legacy_environment
from quantum_light_engine.devices.persistence import FileDeviceProfileRepository
from quantum_light_engine.devices.session_manager import DeviceSessionManager
from quantum_light_engine.observability.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: EngineSettings | None = None) -> FastAPI:
    effective = settings or EngineSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(effective.log_dir, effective.log_level)
        app.state.engine_state = "starting"
        repository = FileDeviceProfileRepository(effective.data_dir)
        await repository.initialize()
        await migrate_legacy_environment(repository)
        manager = DeviceSessionManager(repository, effective)
        await manager.initialize()
        app.state.repository = repository
        app.state.session_manager = manager
        app.state.started_at_utc = datetime.now(UTC)
        app.state.engine_state = "running"
        logger.info("quantum light engine started")
        await manager.auto_start()
        try:
            yield
        finally:
            app.state.engine_state = "stopping"
            await manager.shutdown()
            app.state.engine_state = "stopped"
            logger.info("quantum light engine stopped")
            logging.shutdown()

    app = FastAPI(title="Quantum Light Acquisition Engine", version="0.2.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(engine_router)
    app.include_router(device_router)
    app.include_router(websocket_router)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    return app
