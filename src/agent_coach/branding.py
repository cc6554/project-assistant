"""User-facing product labels.

The package and workspace identifiers intentionally stay stable for existing
installations and persisted data.  UI, help text, and service metadata import
the display labels from this small dependency-free module instead of changing
those compatibility identifiers.
"""

PRODUCT_ID = "agent-coach"
PRODUCT_NAME = "简历项目助手"
PRODUCT_AGENT_NAME = PRODUCT_NAME

__all__ = ["PRODUCT_AGENT_NAME", "PRODUCT_ID", "PRODUCT_NAME"]
