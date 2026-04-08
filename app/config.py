from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    api_keys: str = ""  # comma-separated
    registry_path: str = "./data/registry.db"
    environment: str = "development"  # "production" enforces Cloudflare Access headers
    max_upload_size_mb: int = 50

    @property
    def valid_api_keys(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
