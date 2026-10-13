"""Only the explicit application setting is a GitHub credential source."""
import json


def github_token() -> str | None:
    from ..sidecar.paths import github_token_path
    try:
        value = json.loads(github_token_path().read_text(encoding="utf-8")).get("token")
        return value.strip() or None if isinstance(value, str) else None
    except (OSError, ValueError, AttributeError):
        return None
