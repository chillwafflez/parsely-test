from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str
    azure_di_endpoint: str
    azure_di_key: str
    azure_blob_connection_string: str
    azure_blob_container: str
    cors_allowed_origins: str = "http://localhost:3000"

    # Keycloak issuer URL. Must EXACTLY match the `iss` claim on incoming
    # JWTs — even a trailing slash difference will fail validation.
    keycloak_issuer: str

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def keycloak_jwks_url(self) -> str:
        # OIDC standard path. Override by adding a separate field if you
        # need to fetch JWKS from a different host than the issuer claim
        # (in-cluster vs browser-visible Keycloak — relevant later when
        # api-py runs in K8s alongside Keycloak).
        return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
