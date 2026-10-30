"""
Tests for src/api/.

Nothing here touches the network. A stub session records what would have been
sent and returns canned payloads, so the tests cover the parts that actually
break: pagination shapes, error mapping, and the rule that nothing opens a
browser on its own.
"""

import json
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from api import auth, client  # noqa: E402


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload or {})
        self.content = self.text.encode()

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise AssertionError(f"HTTP {self.status_code}")


class FakeSession:
    """Returns queued responses and records every call."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.queue.pop(0) if self.queue else FakeResponse({})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.queue.pop(0) if self.queue else FakeResponse({})


CREDS = auth.Credentials(
    client_id="id", client_secret="secret",
    access_token="token", refresh_token="refresh",
    expires_at=time.time() + 3600,
)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def test_expiry_uses_a_margin():
    """A token 30s from expiry is treated as expired, so a slow call is safe."""
    nearly = auth.Credentials("i", "s", expires_at=time.time() + 30)
    assert nearly.is_expired()


def test_a_token_with_no_expiry_is_not_assumed_stale():
    assert not auth.Credentials("i", "s", expires_at=0).is_expired()


def test_auth_header_format():
    assert auth.auth_headers(CREDS) == {"Authorization": "OAuth token"}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def test_load_reads_the_config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "client_id": "cid", "client_secret": "csec",
        "access_token": "at", "refresh_token": "rt", "expires_at": 123,
    }))
    creds = auth.load(path)
    assert (creds.client_id, creds.access_token, creds.expires_at) == ("cid", "at", 123)


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"client_id": "from-file", "client_secret": "x"}))
    monkeypatch.setenv("SOUNDCLOUD_CLIENT_ID", "from-env")
    assert auth.load(path).client_id == "from-env"


def test_load_explains_itself_when_credentials_are_missing(tmp_path):
    with pytest.raises(auth.AuthError, match="SOUNDCLOUD_CLIENT_ID"):
        auth.load(tmp_path / "nothing.json")


def test_missing_credentials_are_a_first_launch_not_a_failure(tmp_path):
    """The app opens its setup window on this, so it must be told apart."""
    with pytest.raises(auth.MissingCredentials):
        auth.load(tmp_path / "nothing.json")


def test_an_unedited_example_config_counts_as_missing(tmp_path):
    path = tmp_path / "config.json"
    path.write_text((ROOT / "config.example.json").read_text())
    with pytest.raises(auth.MissingCredentials):
        auth.load(path)


def test_save_round_trips(tmp_path):
    path = tmp_path / "config.json"
    auth.save(CREDS, path)
    assert auth.load(path).refresh_token == "refresh"


# ---------------------------------------------------------------------------
# Refresh — and never opening a browser
# ---------------------------------------------------------------------------

def test_refresh_stores_the_new_token():
    session = FakeSession(FakeResponse({"access_token": "new", "expires_in": 3600}))
    refreshed = auth.refresh(CREDS, session=session)
    assert refreshed.access_token == "new"
    assert refreshed.refresh_token == "refresh"  # kept when not re-issued


def test_refresh_keeps_a_rotated_refresh_token():
    session = FakeSession(
        FakeResponse({"access_token": "new", "refresh_token": "rotated",
                      "expires_in": 3600}))
    assert auth.refresh(CREDS, session=session).refresh_token == "rotated"


def test_a_rejected_refresh_token_asks_for_reauth():
    """400 is how SoundCloud says the grant is dead. Retrying cannot help."""
    session = FakeSession(FakeResponse({}, status_code=400))
    with pytest.raises(auth.ReauthRequired):
        auth.refresh(CREDS, session=session)


def test_refresh_without_a_token_asks_for_reauth():
    with pytest.raises(auth.ReauthRequired):
        auth.refresh(auth.Credentials("i", "s"), session=FakeSession())


def test_being_offline_is_an_auth_error_not_a_crash():
    """A requests exception at startup used to reach the crash dialog."""
    class Offline(FakeSession):
        def post(self, url, **kwargs):
            raise requests.ConnectionError("no route to host")

    with pytest.raises(auth.AuthError, match="internet connection"):
        auth.refresh(CREDS, session=Offline())


def test_a_busy_callback_port_says_so(monkeypatch):
    def busy(*args, **kwargs):
        raise OSError("address already in use")

    monkeypatch.setattr(auth, "HTTPServer", busy)
    with pytest.raises(auth.AuthError, match="8080"):
        auth.authorize_interactively(CREDS, open_browser=False)


def test_a_rejected_secret_points_at_the_apps_page(monkeypatch):
    monkeypatch.setattr(auth.requests, "post",
                        lambda url, **kw: FakeResponse({}, status_code=401))
    with pytest.raises(auth.AuthError, match="client ID and secret"):
        auth._exchange_code(CREDS, "code", "verifier")


def test_a_refused_sign_in_names_the_redirect_uri(monkeypatch):
    monkeypatch.setattr(auth._CallbackHandler, "error", "invalid_client")
    assert auth.REDIRECT_URI in str(auth._no_code("https://x"))


def test_load_fresh_never_opens_a_browser(tmp_path):
    """
    The legacy get_config() opened a consent screen on any refresh failure,
    so no script could safely call it. This must raise instead.
    """
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "client_id": "c", "client_secret": "s",
        "access_token": "old", "refresh_token": "rt",
        "expires_at": time.time() - 1,
    }))
    session = FakeSession(FakeResponse({}, status_code=400))
    with pytest.raises(auth.ReauthRequired):
        auth.load_fresh(path, session=session)


def test_load_fresh_saves_the_refreshed_token(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "client_id": "c", "client_secret": "s",
        "access_token": "old", "refresh_token": "rt",
        "expires_at": time.time() - 1,
    }))
    session = FakeSession(FakeResponse({"access_token": "new", "expires_in": 3600}))
    assert auth.load_fresh(path, session=session).access_token == "new"
    assert json.loads(path.read_text())["access_token"] == "new"


def test_load_fresh_leaves_a_valid_token_alone(tmp_path):
    path = tmp_path / "config.json"
    auth.save(CREDS, path)
    assert auth.load_fresh(path, session=FakeSession()).access_token == "token"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def make_client(*responses):
    session = FakeSession(*responses)
    return client.SoundCloudClient(CREDS, session=session), session


def test_paginates_the_collection_shape():
    api, _ = make_client(
        FakeResponse({"collection": [{"id": 1}], "next_href": "https://x/page2"}),
        FakeResponse({"collection": [{"id": 2}], "next_href": None}),
    )
    assert [t["id"] for t in api.playlist_tracks(9)] == [1, 2]


def test_paginates_the_bare_list_shape():
    """Some endpoints return a plain array with no cursor."""
    api, _ = make_client(FakeResponse([{"id": 1}, {"id": 2}]))
    assert len(api.playlist_tracks(9)) == 2


def test_liked_track_ids_are_strings():
    api, _ = make_client(FakeResponse({"collection": [{"id": 111}, {"id": 222}]}))
    assert api.liked_track_ids() == {"111", "222"}


def test_401_asks_for_reauth():
    api, _ = make_client(FakeResponse({}, status_code=401))
    with pytest.raises(auth.ReauthRequired):
        api.resolve("https://soundcloud.com/x")


def test_404_is_distinct_from_other_failures():
    api, _ = make_client(FakeResponse({}, status_code=404))
    with pytest.raises(client.NotFound):
        api.track(1)


def test_other_errors_carry_the_status_and_body():
    api, _ = make_client(FakeResponse({}, status_code=500, text="boom"))
    with pytest.raises(client.ApiError, match="500"):
        api.track(1)


def test_discovery_playlist_is_created_when_absent():
    api, session = make_client(
        FakeResponse({"collection": [{"id": 1, "title": "other"}]}),  # listing
        FakeResponse({"id": 77}),                                     # create
        FakeResponse({"tracks": []}),                                 # detail
    )
    playlist_id, ids = api.discovery_playlist()
    assert (playlist_id, ids) == (77, [])
    assert any(method == "POST" for method, _, _ in session.calls)


def test_discovery_playlist_is_found_when_present():
    api, session = make_client(
        FakeResponse({"collection": [{"id": 42, "title": "Discovery"}]}),
        FakeResponse({"tracks": [{"id": 5}, {"id": 6}]}),
    )
    assert api.discovery_playlist() == (42, ["5", "6"])
    assert not any(method == "POST" for method, _, _ in session.calls)


def test_set_playlist_tracks_sends_urns():
    """
    SoundCloud has no add/remove endpoint - the whole list is PUT, so anything
    omitted is deleted. Worth pinning.
    """
    api, session = make_client(FakeResponse({}))
    api.set_playlist_tracks(42, ["1", "2"])
    method, url, kwargs = session.calls[-1]
    assert method == "PUT"
    assert kwargs["json"]["playlist"]["tracks"] == [
        {"urn": "soundcloud:tracks:1"},
        {"urn": "soundcloud:tracks:2"},
    ]


# ---------------------------------------------------------------------------
# Find reads
# ---------------------------------------------------------------------------

def test_find_reads_take_one_page_and_never_walk_on():
    """An artist with 900 uploads must cost one request, not five."""
    api, session = make_client(
        FakeResponse({"collection": [{"id": 1}], "next_href": "https://x/2"}))
    assert [t["id"] for t in api.user_tracks(7, limit=50)] == [1]
    assert len(session.calls) == 1
    method, url, kwargs = session.calls[0]
    assert url.endswith("/users/7/tracks")
    assert kwargs["params"]["limit"] == 50


def test_find_reads_accept_the_bare_list_shape():
    """/me/followings/tracks answers with a plain array."""
    api, _ = make_client(FakeResponse([{"id": 1}, {"id": 2}]))
    assert len(api.followings_tracks()) == 2


def test_user_search_sends_the_query():
    api, session = make_client(FakeResponse({"collection": []}))
    api.search_users("Wooli")
    assert session.calls[0][2]["params"]["q"] == "Wooli"


def test_429_is_a_rate_limit_not_a_generic_failure():
    """The search stops and keeps what it found on this one, so it must be told apart."""
    api, _ = make_client(FakeResponse({}, status_code=429))
    with pytest.raises(client.RateLimited):
        api.related_tracks(5)
