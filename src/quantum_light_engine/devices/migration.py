from __future__ import annotations

import logging
import os

from redpitaya_acquisition.config.settings import Settings as LegacySettings

from .models import DeviceProfileCreate, DeviceSource
from .persistence import DeviceProfileRepository

logger = logging.getLogger(__name__)

LEGACY_KEYS = {
    "RP_SOURCE",
    "RP_HOST",
    "RP_CHANNEL_1_ENABLED",
    "RP_CHANNEL_2_ENABLED",
    "RP_DECIMATION",
    "RP_BLOCK_SIZE_BYTES",
    "RP_ADC_BUFFER_SIZE_BYTES",
}


async def migrate_legacy_environment(repository: DeviceProfileRepository) -> bool:
    if await repository.list() or not any(key in os.environ for key in LEGACY_KEYS):
        return False
    legacy = LegacySettings(_env_file=None)
    await repository.create(
        DeviceProfileCreate(
            name="Migrated default device",
            source=DeviceSource(legacy.source.value),
            host=legacy.host if legacy.source.value == "redpitaya" else None,
            is_default=True,
            channel_1_enabled=legacy.channel_1_enabled,
            channel_2_enabled=legacy.channel_2_enabled,
            decimation=legacy.decimation,
            block_size_bytes=legacy.block_size_bytes,
            adc_buffer_size_bytes=legacy.adc_buffer_size_bytes,
            streaming_module_path=legacy.streaming_module,
            simulator_samples_per_packet=legacy.simulator_samples_per_packet,
            simulator_seed=legacy.simulator_seed,
        )
    )
    logger.info("migrated legacy RP_ environment configuration to a persistent profile")
    return True
