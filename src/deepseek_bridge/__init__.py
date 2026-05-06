"""Local proxy connecting AI coding tools to DeepSeek thinking models."""

__all__ = ["__version__"]

try:
    from importlib.metadata import version as _metadata_version
    __version__ = _metadata_version("deepseek-bridge")
except Exception:
    __version__ = "0.0.0"
