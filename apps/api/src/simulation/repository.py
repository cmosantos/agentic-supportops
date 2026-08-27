import json
from pathlib import Path

from domain.simulation import (
    Application,
    Device,
    Host,
    Mailbox,
    SimulationFixture,
    User,
)


DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "contoso.json"


class SimulationRepository:
    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE_PATH) -> None:
        self._fixture_path = fixture_path

    def load_fixture(self) -> SimulationFixture:
        with self._fixture_path.open(encoding="utf-8") as fixture_file:
            return SimulationFixture.model_validate(json.load(fixture_file))

    def get_user(self, reference: str) -> User | None:
        normalized = reference.casefold()
        return next(
            (
                user
                for user in self.load_fixture().environment.users
                if normalized in {user.id.casefold(), user.display_name.casefold(), user.email.casefold()}
            ),
            None,
        )

    def get_mailbox(self, reference: str) -> Mailbox | None:
        normalized = reference.casefold()
        return next(
            (
                mailbox
                for mailbox in self.load_fixture().environment.mailboxes
                if normalized in {mailbox.id.casefold(), mailbox.address.casefold()}
            ),
            None,
        )

    def get_device(self, device_id: str) -> Device | None:
        return next(
            (item for item in self.load_fixture().environment.devices if item.id == device_id.upper()),
            None,
        )

    def get_host(self, host_id: str) -> Host | None:
        return next(
            (item for item in self.load_fixture().environment.hosts if item.id == host_id.upper()),
            None,
        )

    def get_application(self, application_id: str) -> Application | None:
        return next(
            (
                item
                for item in self.load_fixture().environment.applications
                if item.id == application_id.upper()
            ),
            None,
        )
