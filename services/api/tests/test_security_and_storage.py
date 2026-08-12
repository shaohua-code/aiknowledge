from __future__ import annotations

from uuid import uuid4

import pytest

from knowledge_core.infrastructure.storage import LocalObjectStorage
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import ForbiddenError
from knowledge_core.shared.security import generate_api_key, hash_secret, verify_secret


def test_api_key_is_hashed_and_environment_marked() -> None:
    raw, prefix = generate_api_key("production")
    encoded = hash_secret(raw)
    assert raw.startswith("aik_live_")
    assert prefix == raw[:20]
    assert raw not in encoded
    assert verify_secret(raw, encoded)
    assert not verify_secret(f"{raw}x", encoded)


def test_scope_guard_reports_missing_scope() -> None:
    context = ApplicationContext(
        application_id=uuid4(),
        environment_id=uuid4(),
        application_code="resume",
        environment_code="testing",
        api_key_id=uuid4(),
        scopes=frozenset({"knowledge:read"}),
    )
    with pytest.raises(ForbiddenError) as captured:
        context.require("answer:run")
    assert captured.value.details == {"missingScopes": ["answer:run"]}


def test_local_storage_rejects_path_escape(tmp_path) -> None:
    object_storage = LocalObjectStorage(tmp_path)
    with pytest.raises(ValueError, match="越界"):
        object_storage.read("../../outside.txt")


def test_local_storage_uses_application_and_environment_boundaries(tmp_path) -> None:
    object_storage = LocalObjectStorage(tmp_path)
    application_id = uuid4()
    environment_id = uuid4()
    key = object_storage.write(
        application_id,
        environment_id,
        uuid4(),
        uuid4(),
        ".txt",
        b"safe",
    )
    assert str(application_id) in key
    assert str(environment_id) in key
    assert object_storage.read(key) == b"safe"
