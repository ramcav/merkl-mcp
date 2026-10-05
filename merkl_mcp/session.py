"""Joining a receipt to a session this process did not open.

The SDK's ``ReceiptBuilder`` joins "the enclosing session" through a contextvar
(``merkl.sdk.decorators.get_current_session``) that ``async with
client.session(...)`` sets. Here the session lives in the *harness's* process; this
server is only told its id. :class:`JoinedSession` is the smallest adapter that
satisfies what the builder reads (``session_id``, ``action_count``,
``record_action``): a ``SessionContext`` that is never opened or closed by this
process, only appended to. SDK follow-up: an explicit ``session=`` argument on
``ReceiptBuilder.execute`` would make this adapter and the contextvar dance
unnecessary.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from merkl.sdk.decorators import reset_current_session, set_current_session
from merkl.sdk.session_context import SessionContext


class JoinedSession(SessionContext):
    """An already-open session, known by id and by how many actions it holds."""

    def __init__(self, transport: Any, agent_id: str, session_id: str, action_count: int) -> None:
        super().__init__(
            transport=transport,
            agent_id=agent_id,
            goal="",
            allowed_tools=[],
            data_scope=[],
            policy_reference="default",
        )
        self._session_id = session_id
        self._action_count = action_count


@contextlib.contextmanager
def joined(session: JoinedSession | None) -> Iterator[None]:
    """Make ``session`` the current session for the builder, then restore."""
    if session is None:
        yield
        return
    token = set_current_session(session)
    try:
        yield
    finally:
        reset_current_session(token)
