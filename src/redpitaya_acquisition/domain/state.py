from enum import StrEnum


class AcquisitionState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    CONNECTING = "connecting"
    STREAMING = "streaming"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    FAULTED = "faulted"


ACTIVE_STATES = frozenset(
    {
        AcquisitionState.STARTING,
        AcquisitionState.CONNECTING,
        AcquisitionState.STREAMING,
        AcquisitionState.RECONNECTING,
        AcquisitionState.STOPPING,
    }
)
