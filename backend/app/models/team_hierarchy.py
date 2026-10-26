import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class EmploymentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Department(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A configurable org-chart grouping for the Team Hierarchy feature
    (SIGVITAS platform overhaul § 9-10) — deliberately separate from the
    RBAC `Role`/login `User` domain (app/models/rbac.py, app/models/user.py):
    departments group `Employee` directory entries, which model job titles
    like "Software Engineer" or "HR Employee", never platform permissions.

    Soft delete only, same rationale/shape as every other archivable entity
    in this codebase — but additionally, `department_service.delete_department`
    refuses to soft-delete a department that still has active employees
    assigned (CLAUDE.md § 10: "do not cascade-delete employees
    accidentally"); employees must be moved or deactivated first.
    """

    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Employee(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """An HR/org-chart directory entry — deliberately a separate concept
    from `User` (portal login + RBAC role). Most employees in the hierarchy
    (e.g. "Patent Engineer", multiple "Software Engineer"s) never need a
    portal login at all; `user_id` optionally links an employee to one
    portal account when it exists, but is never required.

    `department_id`/`manager_id` are `ON DELETE SET NULL` — removing a
    department or a manager's own row (soft-delete keeps the row, but this
    stays correct even if that policy ever changes) reassigns rather than
    destroys the employees that pointed to it.

    `display_order` is the hidden position of an employee among the others in
    the same (organization, department) — `department_id IS NULL` being the
    "Unassigned" group — and is what the org chart is sorted by. It is
    internal: never shown to users and never part of an API response.
    Assigned and kept dense (1..n) by `employee_service`, which serializes
    writers per scope with an advisory lock; deliberately an index rather
    than a unique constraint, since renumbering a scope row by row would
    collide mid-update and NULL departments would need `NULLS NOT DISTINCT`.
    """

    __tablename__ = "employees"
    __table_args__ = (
        Index("ix_employees_org_dept_display_order", "organization_id", "department_id", "display_order"),
        # One active record per email; a removed (soft-deleted) employee's
        # email can be reused (migration e8f9a0b1c2d3).
        Index(
            "uq_employees_org_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    employee_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    joining_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    employment_status: Mapped[EmploymentStatus] = mapped_column(
        Enum(EmploymentStatus, name="employment_status", native_enum=True),
        nullable=False,
        default=EmploymentStatus.ACTIVE,
        server_default=EmploymentStatus.ACTIVE.value,
    )
    # Optional link to a portal login account — most employees won't have
    # one (CLAUDE.md: SUPER_ADMIN and RBAC roles never appear here either
    # way, since `designation` is free text, not a Role).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
