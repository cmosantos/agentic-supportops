import json
from pathlib import Path
from typing import Iterable

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
    def __init__(
        self,
        fixture_path: Path = DEFAULT_FIXTURE_PATH,
        *,
        restart_outcomes: dict[str, str] | None = None,
        locked_users: Iterable[str] = (),
        unobservable_applications: Iterable[str] = (),
    ) -> None:
        self._fixture_path = fixture_path
        self._application_status: dict[str, str] = {}
        self._user_locked = {user_id.upper(): True for user_id in locked_users}
        self._restart_outcomes = {
            key.upper(): value for key, value in (restart_outcomes or {}).items()
        }
        self._unobservable_applications = {
            application_id.upper() for application_id in unobservable_applications
        }

    def load_fixture(self) -> SimulationFixture:
        with self._fixture_path.open(encoding="utf-8") as fixture_file:
            return SimulationFixture.model_validate(json.load(fixture_file))

    def get_user(self, reference: str) -> User | None:
        normalized = reference.casefold()
        user = next(
            (
                user
                for user in self.load_fixture().environment.users
                if normalized in {user.id.casefold(), user.display_name.casefold(), user.email.casefold()}
            ),
            None,
        )
        if user is None:
            return None
        locked = self._user_locked.get(user.id, user.account.locked)
        return user.model_copy(
            update={"account": user.account.model_copy(update={"locked": locked})}
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
        normalized = application_id.upper()
        if normalized in self._unobservable_applications:
            raise RuntimeError("Simulated health observation unavailable")
        return self.get_application_for_action(normalized)

    def get_application_for_action(self, application_id: str) -> Application | None:
        normalized = application_id.upper()
        application = next(
            (
                item
                for item in self.load_fixture().environment.applications
                if item.id == normalized
            ),
            None,
        )
        if application is None:
            return None
        status = self._application_status.get(normalized)
        return application.model_copy(update={"status": status}) if status else application

    def restart_application(self, application_id: str) -> str:
        normalized = application_id.upper()
        status = self._restart_outcomes.get(normalized, "healthy")
        self._application_status[normalized] = status
        return status

    def unlock_user(self, user_id: str) -> bool:
        normalized = user_id.upper()
        self._user_locked[normalized] = False
        return self._user_locked[normalized]

    def reset_application(self, application_id: str) -> str:
        normalized = application_id.upper()
        self._application_status[normalized] = "healthy"
        return self._application_status[normalized]
