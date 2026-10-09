"""Projects of the caller's workspace (KI-027: the web resolves names to ids through this)."""

from typing import Annotated, Any

from abb_event_schema.ids import IdKind
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.domain import AlreadyExistsError
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope
from abb_api.ids import public_id
from abb_api.projects.repository import Project, ProjectRepository

router = APIRouter(prefix="/v1/projects", tags=["projects"])

Reader = Annotated[Principal, Depends(require(actions.PROJECT_READ))]
Writer = Annotated[Principal, Depends(require(actions.PROJECT_WRITE))]

MAX_PROJECTS = 200  # per workspace: no client-controlled unbounded cardinality (Phase 15 plan)

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        401: "Missing or invalid credential.",
        403: "The caller may not create projects.",
        404: "Workspace not found, or the caller is not a member.",
        409: "The slug is taken, or the workspace has reached its project limit.",
        422: "The request body is invalid.",
        503: "A dependency is unavailable; retry with backoff.",
    }.items()
}

Name = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f-\x9f]+$")
]


class ProjectOut(BaseModel):
    id: str
    slug: str
    name: str


class ProjectList(BaseModel):
    items: list[ProjectOut]


class CreateProject(BaseModel):
    name: Name
    slug: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]{0,62}$", description="Lowercase, digits, hyphens."
    )


def _out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=public_id(IdKind.PROJECT, project.id), slug=project.slug, name=project.name
    )


@router.get(
    "",
    response_model=ProjectList,
    responses=_ERRORS,
    summary="List the projects the caller may see",
    description=(
        "Every project of the workspace for a person or a workspace-wide key; a project-bound key "
        f"sees only its own. At most {MAX_PROJECTS} (the per-workspace limit), so no paging."
    ),
)
async def list_projects(request: Request, principal: Reader) -> ProjectList:
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn:
        projects = await ProjectRepository(conn, principal.tenant).list()
    if principal.project_id is not None:
        projects = [p for p in projects if p.id == principal.project_id]
    return ProjectList(items=[_out(p) for p in projects])


@router.post(
    "",
    status_code=201,
    response_model=ProjectOut,
    responses=_ERRORS,
    summary="Create a project",
    description=f"Owners and admins only. A workspace holds at most {MAX_PROJECTS} projects.",
)
async def create_project(
    body: CreateProject, request: Request, response: Response, principal: Writer
) -> ProjectOut:
    engine: AsyncEngine = request.app.state.engine
    async with engine.begin() as conn:
        projects = ProjectRepository(conn, principal.tenant)
        await projects.lock_for_create()
        if await projects.count() >= MAX_PROJECTS:
            raise AppError(
                "LIMIT_REACHED",
                f"A workspace holds at most {MAX_PROJECTS} projects.",
                category=ErrorCategory.CONFLICT,
                status_code=409,
            )
        try:
            created = await projects.create(name=body.name, slug=body.slug)
        except AlreadyExistsError:
            raise AppError(
                "PROJECT_EXISTS",
                "A project with that slug already exists.",
                category=ErrorCategory.CONFLICT,
                status_code=409,
            ) from None
    return _out(created)
