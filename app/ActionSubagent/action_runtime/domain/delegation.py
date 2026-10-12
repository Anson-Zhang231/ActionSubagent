"""Business identity asserted by a trusted supervisor."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

type Identifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=re.compile(r"^[a-z][a-z0-9_-]*(?![\s\S])"),
    ),
]
_ID_EDGE_EXCLUDED = (
    r"\u0000-\u0020\u007f\u0085\u00a0\u1680\u2000-\u200a"
    r"\u2028\u2029\u202f\u205f\u3000"
)
ExternalUserId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=re.compile(
            rf"^[^{_ID_EDGE_EXCLUDED}]"
            rf"(?:[^\u0000-\u001f\u007f]*[^{_ID_EDGE_EXCLUDED}])?(?![\s\S])"
        ),
    ),
]


class DelegatedUserContext(BaseModel):
    """Business identity asserted by the trusted supervisor, never model-generated grants."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: Identifier
    organization_id: Identifier
    department_id: Identifier
    application_user_ids: dict[Identifier, ExternalUserId] = Field(max_length=32)
