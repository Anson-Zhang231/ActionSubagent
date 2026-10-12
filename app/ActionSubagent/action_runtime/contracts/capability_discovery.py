from pydantic import BaseModel, ConfigDict, Field


class DiscoveredCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    application_name: str = Field(min_length=1)

    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    capability_version: str = Field(
        pattern=(
            r"^(0|[1-9][0-9]*)\."
            r"(0|[1-9][0-9]*)\."
            r"(0|[1-9][0-9]*)"
            r"(?:-[0-9A-Za-z.-]+)?"
            r"(?:\+[0-9A-Za-z.-]+)?$"
        )
    )

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)

    required_parameters: list[str]
    optional_parameters: list[str]


class CapabilityDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*$",
    )

    capabilities: list[DiscoveredCapability]
    total: int = Field(ge=0)

    registry_revision: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    message: str = Field(min_length=1)
