from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    api_keys: str = ""  # comma-separated
    registry_path: str = "./data/registry.db"
    environment: str = "development"  # "production" enforces Cloudflare Access headers
    max_upload_size_mb: int = 50
    base_url: str = "https://private-mcp.propiolatam.com"
    pg_user: str = "mcpbridge"
    pg_password: str = "mcpbridge"
    pg_host: str = "localhost"
    pg_port: int = 5432

    @property
    def valid_api_keys(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def first_api_key(self) -> str:
        """First configured API key, used for setup script generation."""
        keys = self.valid_api_keys
        return next(iter(keys)) if keys else ""

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
