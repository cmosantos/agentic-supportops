from domain.investigation import ToolErrorCode, ToolResult
from simulation.repository import SimulationRepository
from tools.common import failure, success


class IdentityTools:
    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository

    def get_user(self, reference: str) -> ToolResult:
        user = self._repository.get_user(reference)
        if user is None:
            return failure("get_user", reference, ToolErrorCode.USER_NOT_FOUND, "User not found")
        return success("get_user", user.id, user.model_dump())

    def get_account_status(self, user_id: str) -> ToolResult:
        user = self._repository.get_user(user_id)
        if user is None:
            return failure(
                "get_account_status", user_id, ToolErrorCode.USER_NOT_FOUND, "User not found"
            )
        return success("get_account_status", user.id, user.account.model_dump())

    def get_user_groups(self, user_id: str) -> ToolResult:
        user = self._repository.get_user(user_id)
        if user is None:
            return failure("get_user_groups", user_id, ToolErrorCode.USER_NOT_FOUND, "User not found")
        return success("get_user_groups", user.id, {"groups": user.groups})

    def get_user_licenses(self, user_id: str) -> ToolResult:
        user = self._repository.get_user(user_id)
        if user is None:
            return failure(
                "get_user_licenses", user_id, ToolErrorCode.USER_NOT_FOUND, "User not found"
            )
        return success("get_user_licenses", user.id, {"licenses": user.licenses})

    def get_mailbox(self, reference: str) -> ToolResult:
        mailbox = self._repository.get_mailbox(reference)
        if mailbox is None:
            return failure(
                "get_mailbox", reference, ToolErrorCode.MAILBOX_NOT_FOUND, "Mailbox not found"
            )
        data = mailbox.model_dump(exclude={"permissions"})
        data["usage_percent"] = round(
            mailbox.used_gb / mailbox.quota_gb * 100, 2
        ) if mailbox.quota_gb else 0
        return success("get_mailbox", mailbox.id, data)

    def get_mailbox_permissions(self, mailbox_id: str, user_id: str) -> ToolResult:
        mailbox = self._repository.get_mailbox(mailbox_id)
        if mailbox is None:
            return failure(
                "get_mailbox_permissions",
                mailbox_id,
                ToolErrorCode.MAILBOX_NOT_FOUND,
                "Mailbox not found",
            )
        if self._repository.get_user(user_id) is None:
            return failure(
                "get_mailbox_permissions", user_id, ToolErrorCode.USER_NOT_FOUND, "User not found"
            )
        permission = next((item for item in mailbox.permissions if item.user_id == user_id), None)
        data = permission.model_dump() if permission else {
            "user_id": user_id,
            "full_access": False,
            "send_as": False,
            "automapping": False,
            "deny": False,
        }
        return success("get_mailbox_permissions", mailbox.id, data)

