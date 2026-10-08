"""Which project a caller may ask about: the one place a project filter is authorised (INV-3)."""

import uuid

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import parse_public_id
from abb_api.projects.repository import ProjectRepository
from abb_api.tenancy import Principal


def project_not_found() -> AppError:
    return AppError(
        "PROJECT_NOT_FOUND", "Project not found.", category=ErrorCategory.NOT_FOUND, status_code=404
    )


async def authorise_project(
    conn: AsyncConnection, principal: Principal, project_id: str | None
) -> uuid.UUID | None:
    """The project to filter by, or None for "everything this key may see".

    A project-bound key is always confined to its own project: asking for another is "not found",
    exactly like an unknown project, so keys cannot probe. A workspace-wide key may name any project
    of its workspace; one that is not in the workspace is also "not found".
    """
    if project_id is None:
        return principal.project_id
    requested = parse_public_id(IdKind.PROJECT, project_id)
    if requested is None:
        raise project_not_found()
    if principal.project_id is not None:
        if requested != principal.project_id:
            raise project_not_found()
    elif await ProjectRepository(conn, principal.tenant).get(requested) is None:
        raise project_not_found()
    return requested
