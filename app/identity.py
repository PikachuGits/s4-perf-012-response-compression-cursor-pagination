"""Minimal tenant identity for a self-contained harness.

The real platform resolves the tenant from the signed-in user. Here it comes
from headers so the test suite can exercise tenant isolation without an IAM
dependency (S2-IAM is a separate work item).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Header

from .constants import DEMO_ORG, DEMO_SITE, DEMO_USER


@dataclass(frozen=True)
class Identity:
    org_id: uuid.UUID
    site_id: uuid.UUID
    user_id: uuid.UUID


def identity(
    x_org_id: uuid.UUID | None = Header(default=None, alias="X-Org-Id"),
    x_site_id: uuid.UUID | None = Header(default=None, alias="X-Site-Id"),
    x_user_id: uuid.UUID | None = Header(default=None, alias="X-User-Id"),
) -> Identity:
    return Identity(
        org_id=x_org_id or DEMO_ORG,
        site_id=x_site_id or DEMO_SITE,
        user_id=x_user_id or DEMO_USER,
    )
