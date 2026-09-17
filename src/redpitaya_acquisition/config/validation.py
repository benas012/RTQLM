from .settings import Settings


def validate_settings(settings: Settings) -> Settings:
    """Explicit validation hook for embedders and tests."""
    return Settings.model_validate(settings.model_dump())
