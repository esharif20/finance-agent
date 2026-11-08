"""#298: the Gemini cloud client. No test reaches the network or the Keychain:
`FakeCloud` and `RecordedTransport` stand in, and the conftest egress guard
allows no cloud host."""

import json
import logging
import pickle
from pathlib import Path

import pytest

from conftest import ALLOWED_EGRESS
from finance_agent.gateway import GatewayPayload, Money, ToolFact, cloud, redact
from finance_agent.gateway.cloud import (
    CloudChatModel,
    CloudError,
    FakeCloud,
    RecordedTransport,
    Refused,
    Usage,
)

RECORDINGS = Path(__file__).parent / "fixtures" / "synthetic" / "cloud"
KEY = "fake-fake-fake"  # synthetic; new-format keys have no fixed prefix


def _payload() -> GatewayPayload:
    payload, _ = redact(
        "How much did I spend on groceries last month?",
        [ToolFact(field="spending", value=Money(minor=4250))],
    )
    return payload


def _raw(reply: bytes) -> cloud.Transport:
    def transport(url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        return 200, reply

    return transport


def test_the_cloud_host_is_not_allowlisted_in_the_test_run() -> None:
    assert cloud.HOST not in {h for h, _ in ALLOWED_EGRESS}


def test_the_key_goes_in_the_header_and_never_the_url() -> None:
    fake = FakeCloud()
    CloudChatModel(KEY, transport=fake).complete(_payload(), model="m1", max_tokens=64)
    ((url, headers, body),) = fake.calls
    assert url == f"https://{cloud.HOST}/v1beta/models/m1:generateContent"
    assert headers["x-goog-api-key"] == KEY
    assert KEY not in url and KEY.encode() not in body
    sent = json.loads(body)
    assert sent["generationConfig"] == {"maxOutputTokens": 64}
    assert json.loads(sent["contents"][0]["parts"][0]["text"]) == json.loads(
        _payload().model_dump_json()
    )


def test_usage_tokens_come_back_exactly() -> None:
    fake = FakeCloud(
        text="You spent {P1}.",
        usage={
            "promptTokenCount": 7,
            "candidatesTokenCount": 3,
            "thoughtsTokenCount": 41,
        },
        model_version="gemini-3.8-flash",
    )
    text, usage = CloudChatModel(KEY, transport=fake).complete(
        _payload(), model="gemini-flash-latest", max_tokens=256
    )
    assert text == "You spent {P1}."
    assert usage == Usage(7, 3, 41, "gemini-3.8-flash")


@pytest.mark.parametrize(
    ("name", "version", "thinking"),
    [
        ("gemini-3.8-flash-8df02fe30c39.json", "gemini-3.8-flash", 107),
        ("gemini-flash-latest-334d0d446ce5.json", "gemini-3.8-flash", 81),
        ("gemini-pro-latest-87389a2fa3f3.json", "gemini-3.1-pro-preview", 178),
    ],
)
def test_recordings_replay_by_payload_hash(
    name: str, version: str, thinking: int
) -> None:
    model = cloud.load_recording(RECORDINGS / name)["model"]
    llm = CloudChatModel(KEY, transport=RecordedTransport(RECORDINGS))
    text, usage = llm.complete(_payload(), model=model, max_tokens=256)
    assert text == "ready"
    assert usage == Usage(11, 1, thinking, version)  # the alias records what ran


def test_an_unknown_payload_hash_fails() -> None:
    llm = CloudChatModel(KEY, transport=RecordedTransport(RECORDINGS))
    with pytest.raises(CloudError):
        llm.complete(_payload(), model="gemini-flash-latest", max_tokens=255)


def test_every_recording_is_synthetic_and_keyless() -> None:
    files = sorted(RECORDINGS.glob("*.json"))
    assert files
    for p in files:
        text = p.read_text()
        assert text.startswith("# synthetic\n")
        assert "x-goog-api-key" not in text and "AIza" not in text


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example",
        f"http://{cloud.HOST}",
        f"https://{cloud.HOST}:8443",
        f"https://{cloud.HOST}.evil.example",
        f"https://user:pw@{cloud.HOST}",
        f"https://{cloud.HOST}/other",
        f"https://{cloud.HOST}?x=1",
    ],
)
def test_a_non_pinned_base_url_is_refused(url: str) -> None:
    with pytest.raises(CloudError):
        CloudChatModel(KEY, transport=FakeCloud(), base_url=url)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_from_the_transport_is_refused(status: int) -> None:
    llm = CloudChatModel(KEY, transport=FakeCloud(status=status))
    with pytest.raises(CloudError, match=str(status)):
        llm.complete(_payload(), model="m", max_tokens=8)


def test_https_proxy_in_the_environment_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A proxied request would dial 127.0.0.1:9; a direct one dials the host,
    # which the egress guard stops before any packet leaves.
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    url = f"https://{cloud.HOST}/v1beta/models/m:generateContent"
    with pytest.raises(OSError, match=f"egress to {cloud.HOST}:443"):
        cloud.urllib_transport(url, {}, b"{}")


def test_the_real_transport_refuses_another_host() -> None:
    with pytest.raises(CloudError):
        cloud.urllib_transport("https://evil.example/v1beta/models/m:x", {}, b"{}")


@pytest.mark.parametrize(
    "finish", ["SAFETY", "RECITATION", "PROHIBITED_CONTENT", "OTHER"]
)
def test_a_refusal_is_a_rejected_reply(finish: str) -> None:
    llm = CloudChatModel(KEY, transport=FakeCloud(finish=finish))
    with pytest.raises(Refused):
        llm.complete(_payload(), model="m", max_tokens=8)


def test_a_blocked_prompt_is_a_rejected_reply() -> None:
    blocked = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()
    llm = CloudChatModel(KEY, transport=_raw(blocked))
    with pytest.raises(Refused):
        llm.complete(_payload(), model="m", max_tokens=8)


def test_thinking_that_eats_max_tokens_is_an_error_not_an_empty_reply() -> None:
    llm = CloudChatModel(KEY, transport=FakeCloud(text="", finish="MAX_TOKENS"))
    with pytest.raises(CloudError, match="max_tokens"):
        llm.complete(_payload(), model="m", max_tokens=8)


def test_one_retry_then_a_class_and_count_error() -> None:
    calls: list[int] = []

    def flaky(url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        calls.append(1)
        raise OSError(f"boom {url} {headers}")

    llm = CloudChatModel(KEY, transport=flaky)
    with pytest.raises(CloudError) as err:
        llm.complete(_payload(), model="m", max_tokens=8)
    assert len(calls) == 1 + cloud.MAX_RETRIES
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert KEY not in str(err.value) and "boom" not in str(err.value)


def test_a_retryable_status_is_retried_once() -> None:
    replies = [FakeCloud(status=503), FakeCloud()]
    llm = CloudChatModel(KEY, transport=lambda u, h, b: replies.pop(0)(u, h, b))
    assert llm.complete(_payload(), model="m", max_tokens=8)[0] == "ready"


def test_the_key_never_appears_in_repr_logs_or_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    llm = CloudChatModel(KEY, transport=FakeCloud(status=500))
    assert KEY not in repr(llm) and KEY not in str(llm)
    with pytest.raises(TypeError):
        pickle.dumps(llm)
    with pytest.raises(CloudError) as err:
        llm.complete(_payload(), model="m", max_tokens=8)
    assert KEY not in str(err.value) and KEY not in repr(err.value)
    assert KEY not in caplog.text


@pytest.mark.parametrize("key", ["", "has space", "line\nbreak", "tab\t"])
def test_a_malformed_key_is_refused_without_echoing_it(key: str) -> None:
    with pytest.raises(CloudError) as err:
        CloudChatModel(key, transport=FakeCloud())
    assert not key.strip() or key not in str(err.value)


@pytest.mark.parametrize("model", ["../x", "m/evil", "M", "m?key=1", ""])
def test_a_model_id_cannot_change_the_path(model: str) -> None:
    llm = CloudChatModel(KEY, transport=FakeCloud())
    with pytest.raises(CloudError):
        llm.complete(_payload(), model=model, max_tokens=8)


def test_a_hand_built_object_is_refused_at_runtime_too() -> None:
    llm = CloudChatModel(KEY, transport=FakeCloud())
    with pytest.raises(TypeError):
        llm.complete("raw text", model="m", max_tokens=8)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    "reply",
    [b"not json", b"[]", b'{"candidates": [{"finishReason": "STOP"}]}'],
)
def test_an_unreadable_reply_is_a_cloud_error(reply: bytes) -> None:
    llm = CloudChatModel(KEY, transport=_raw(reply))
    with pytest.raises(CloudError):
        llm.complete(_payload(), model="m", max_tokens=8)


def test_a_recording_never_holds_the_key(tmp_path: Path) -> None:
    echo = FakeCloud(text=f"leaked {KEY}")
    rec = cloud.RecordingTransport(tmp_path, KEY, inner=echo)
    with pytest.raises(CloudError):
        CloudChatModel(KEY, transport=rec).complete(_payload(), model="m", max_tokens=8)
    assert list(tmp_path.iterdir()) == []


def test_a_recording_round_trips(tmp_path: Path) -> None:
    rec = cloud.RecordingTransport(tmp_path, KEY, inner=FakeCloud())
    CloudChatModel(KEY, transport=rec).complete(_payload(), model="m", max_tokens=8)
    replay = CloudChatModel(KEY, transport=RecordedTransport(tmp_path))
    assert replay.complete(_payload(), model="m", max_tokens=8)[0] == "ready"
