from pydantic import BaseModel, Field


class AccountState(BaseModel):
    enabled: bool
    locked: bool = False


class User(BaseModel):
    id: str
    display_name: str
    email: str
    account: AccountState
    groups: list[str]
    licenses: list[str]


class MailboxPermission(BaseModel):
    user_id: str
    full_access: bool
    send_as: bool
    automapping: bool
    deny: bool = False


class Mailbox(BaseModel):
    id: str
    address: str
    display_name: str
    mailbox_type: str
    quota_gb: float
    used_gb: float
    accepts_external: bool = True
    permissions: list[MailboxPermission] = Field(default_factory=list)


class NetworkConfig(BaseModel):
    interface: str
    ip_address: str
    gateway: str
    dns_servers: list[str]
    gateway_reachable: bool
    external_reachable: bool
    dns_operational: bool


class ServiceState(BaseModel):
    name: str
    status: str
    healthy: bool


class Device(BaseModel):
    id: str
    operating_system: str
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    uptime_hours: float
    network: NetworkConfig
    services: list[ServiceState]


class Host(BaseModel):
    id: str
    host_type: str
    status: str
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    services: list[ServiceState]


class Alert(BaseModel):
    id: str
    host_id: str
    severity: str
    message: str
    active: bool


class Application(BaseModel):
    id: str
    host_id: str
    status: str
    latency_ms: float
    error_rate_percent: float
    connection_pool_percent: float | None = None


class SimulationEnvironment(BaseModel):
    organization: str
    users: list[User]
    mailboxes: list[Mailbox]
    devices: list[Device]
    hosts: list[Host]
    alerts: list[Alert]
    applications: list[Application]


class CatalogIncident(BaseModel):
    catalog_id: str
    title: str
    description: str
    category: str
    priority: str
    requester: str
    affected_resource_type: str
    affected_resource_id: str
    investigation_context: dict[str, str] = Field(default_factory=dict)


class SimulationFixture(BaseModel):
    environment: SimulationEnvironment
    incidents: list[CatalogIncident]

