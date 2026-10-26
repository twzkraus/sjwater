DOMAIN = "sjwater"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"


def entry_id_slug(entry_id: str) -> str:
    """Make a Home Assistant config entry ID safe to include in an entity ID."""
    return entry_id.replace("-", "_")
