# pyright: reportUnnecessaryTypeIgnoreComment=true
"""#298: pyright, not pytest, checks this file. Each ignore must stay needed:
if `complete` ever accepted a non-payload, the ignore would become unnecessary
and pyright would fail the build."""

from finance_agent.gateway.cloud import CloudChatModel, FakeCloud


def _never_called(cloud: CloudChatModel) -> None:
    cloud.complete("raw question text", model="m", max_tokens=8)  # pyright: ignore[reportArgumentType]
    cloud.complete({"question": "q"}, model="m", max_tokens=8)  # pyright: ignore[reportArgumentType]


_ = (_never_called, FakeCloud)
