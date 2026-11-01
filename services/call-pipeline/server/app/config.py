from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPELINE_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./pipeline.db"
    storage_root: str = "/var/lib/call-pipeline"
    db_encryption_key: str = ""        # required for prod; fixtures set in tests
    anthropic_api_key: str = ""
    hf_token: str = ""
    cors_origins: str = ""


settings = Settings()
