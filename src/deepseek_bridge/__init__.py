"""Local proxy connecting AI coding tools to DeepSeek thinking models."""

__all__ = ["__version__"]

try:
    from importlib.metadata import version as _metadata_version

    __version__ = _metadata_version("deepseek-bridge")
except Exception as exc:
    __version__ = "0.0.0"
    import logging as _stdlib_logging

    _stdlib_logging.getLogger("deepseek_bridge").warning(
        "failed to determine package version: %s", exc
    )
