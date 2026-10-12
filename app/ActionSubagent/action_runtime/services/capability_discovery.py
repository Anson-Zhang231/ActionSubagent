from action_runtime.application_registry import (
    ApplicationRegistry,
    RegisteredApplication,
)
from action_runtime.capability_registry import (
    CapabilityRegistry,
)
from action_runtime.contracts.capability_discovery import (
    CapabilityDiscoveryResult,
    DiscoveredCapability,
)


class CapabilityDiscoveryService:
    def __init__(
        self,
        *,
        application_registry: ApplicationRegistry,
        capability_registry: CapabilityRegistry,
    ) -> None:
        self._application_registry = application_registry
        self._capability_registry = capability_registry

    def discover(
        self,
        *,
        application_id: str | None = None,
    ) -> CapabilityDiscoveryResult:
        applications = self._resolve_applications(application_id)
        capabilities = self._build_capabilities(applications)

        return CapabilityDiscoveryResult(
            application_id=application_id,
            capabilities=capabilities,
            total=len(capabilities),
            registry_revision=(self._capability_registry.revision),
            message=self._build_message(
                applications,
                capabilities,
            ),
        )

    def _resolve_applications(
        self,
        application_id: str | None,
    ) -> list[RegisteredApplication]:
        if application_id is None:
            return self._application_registry.list_latest_active()

        return [
            self._application_registry.resolve(
                application_id=application_id,
            )
        ]

    def _build_capabilities(
        self,
        applications: list[RegisteredApplication],
    ) -> list[DiscoveredCapability]:
        discovered: list[DiscoveredCapability] = []

        for application_entry in applications:
            application = application_entry.application

            for capability_entry in self._capability_registry.list_latest_active(application.id):
                capability = capability_entry.capability
                input_schema = capability.input_schema
                properties = input_schema["properties"]
                required = set(input_schema.get("required", []))

                required_parameters = [name for name in properties if name in required]
                optional_parameters = [name for name in properties if name not in required]

                discovered.append(
                    DiscoveredCapability(
                        application_id=application.id,
                        application_name=application.name,
                        capability_id=capability.id,
                        capability_version=(capability.version),
                        name=capability.name,
                        description=capability.description,
                        required_parameters=(required_parameters),
                        optional_parameters=(optional_parameters),
                    )
                )

        return discovered

    @staticmethod
    def _build_message(
        applications: list[RegisteredApplication],
        capabilities: list[DiscoveredCapability],
    ) -> str:
        if len(applications) == 1:
            application = applications[0].application
            action_names = [capability.name for capability in capabilities]

            if not action_names:
                return f"{application.name} has no active actions registered."

            return (
                f"{application.name} supports "
                f"{len(action_names)} active actions: "
                f"{', '.join(action_names)}."
            )

        groups: list[str] = []

        for application_entry in applications:
            application = application_entry.application
            action_names = [
                capability.name
                for capability in capabilities
                if (capability.application_id == application.id)
            ]

            groups.append(f"{application.name}: {', '.join(action_names)}")

        return "Available actions by application: " + "; ".join(groups) + "."
