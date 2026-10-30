"""
Typed calls to the SoundCloud API.

The only module that talks to soundcloud.com. Everything returns plain dicts or
ids; turning those into TrackRefs is services/'s job. Pagination and the
OAuth header live here so no caller has to remember them.

Public: SoundCloudClient, ApiError, NotFound, RateLimited,
        DISCOVERY_PLAYLIST_TITLE

Imports api.auth only. Never imports core/, data/, services/ or ui/.
"""

from __future__ import annotations

from typing import Iterable, Iterator

import requests

from .auth import Credentials, ReauthRequired, auth_headers

API_BASE = "https://api.soundcloud.com"
DISCOVERY_PLAYLIST_TITLE = "discovery"
PAGE_SIZE = 200
TIMEOUT_SECONDS = 30


class ApiError(Exception):
    """SoundCloud refused or failed a request."""


class NotFound(ApiError):
    """The resource does not exist, or is not visible to this account."""


class RateLimited(ApiError):
    """SoundCloud said slow down (429). A search stops and keeps what it has."""


class SoundCloudClient:
    """
    A session-backed wrapper around the endpoints this app uses.

    Construct it with credentials that are already fresh - refreshing is
    auth.load_fresh()'s job, so this class never opens a browser or writes
    config.json.
    """

    def __init__(self, credentials: Credentials,
                 session: requests.Session | None = None):
        self.credentials = credentials
        self.session = session or requests.Session()

    # -- plumbing ----------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> dict | list:
        if not url.startswith("http"):
            url = f"{API_BASE}{url}"
        headers = {
            **auth_headers(self.credentials),
            "Accept": "application/json; charset=utf-8",
            **kwargs.pop("headers", {}),
        }
        try:
            response = self.session.request(
                method, url, headers=headers, timeout=TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as exc:
            raise ApiError(f"{method} {url} failed: {exc}") from exc

        if response.status_code == 401:
            raise ReauthRequired("SoundCloud rejected the access token (401).")
        if response.status_code == 404:
            raise NotFound(f"{method} {url} -> 404")
        if response.status_code == 429:
            raise RateLimited(f"{method} {url} -> 429: rate limited by SoundCloud")
        if not response.ok:
            raise ApiError(
                f"{method} {url} -> HTTP {response.status_code}: {response.text[:300]}"
            )
        if not response.content:
            return {}
        return response.json()

    def _paginate(self, url: str, params: dict | None = None) -> Iterator[dict]:
        """
        Walk a linked_partitioning endpoint to the end.

        SoundCloud returns either a bare list or {collection, next_href}
        depending on the endpoint, so both shapes are handled.
        """
        query = {"limit": PAGE_SIZE, "linked_partitioning": "true", **(params or {})}
        while url:
            payload = self._request("GET", url, params=query)
            if isinstance(payload, dict):
                yield from payload.get("collection", [])
                url = payload.get("next_href")
            else:
                yield from payload
                url = None
            query = {}  # next_href already carries the cursor

    # -- reads -------------------------------------------------------------

    def resolve(self, url: str) -> dict:
        """Turn a soundcloud.com URL into the resource it points at."""
        return self._request("GET", "/resolve", params={"url": url})

    def playlist_tracks(self, playlist_id: int | str) -> list:
        """Every track in a playlist."""
        return list(self._paginate(f"/playlists/{playlist_id}/tracks"))

    def liked_tracks(self) -> list:
        """
        Every track in the user's likes, with durations and titles.

        Needed to tell a liked SET from a liked song - the 20-minute rule -
        when adopting things hearted outside the Discovery loop.
        """
        return list(self._paginate("/me/likes/tracks"))

    def liked_track_ids(self) -> set:
        """
        Every track id in the user's likes.

        This is the whole likes list, not just sets this app knows about - it
        holds far more than the app has ever seen, so callers intersect it with
        their own ids.
        """
        return {str(t["id"]) for t in self._paginate("/me/likes/tracks")}

    def track(self, track_id: int | str) -> dict:
        return self._request("GET", f"/tracks/{track_id}")

    # -- reads for the Find search ------------------------------------------
    # One page each, never a full walk: an artist with 900 uploads would
    # otherwise cost five requests to read, and a search makes dozens of these.

    def _first_page(self, url: str, limit: int, params: dict | None = None
                    ) -> list:
        query = {"limit": limit, "linked_partitioning": "true", **(params or {})}
        payload = self._request("GET", url, params=query)
        if isinstance(payload, dict):
            return list(payload.get("collection", []))
        return list(payload)

    def search_users(self, query: str, limit: int = 10) -> list:
        """Accounts matching a name, best matches first."""
        return self._first_page("/users", limit, {"q": query})

    def user_tracks(self, user_id: int | str, limit: int = 50) -> list:
        """An account's own uploads, newest first."""
        return self._first_page(f"/users/{user_id}/tracks", limit)

    def user_liked_tracks(self, user_id: int | str, limit: int = 50) -> list:
        """Tracks an account has liked, most recent first."""
        return self._first_page(f"/users/{user_id}/likes/tracks", limit)

    def user_followings(self, user_id: int | str, limit: int = 200) -> list:
        """Accounts an account follows."""
        return self._first_page(f"/users/{user_id}/followings", limit)

    def followings_tracks(self, limit: int = 100) -> list:
        """Recent uploads from accounts the signed-in user follows."""
        return self._first_page("/me/followings/tracks", limit)

    def related_tracks(self, track_id: int | str, limit: int = 20) -> list:
        """SoundCloud's own 'related tracks' for one track."""
        return self._first_page(f"/tracks/{track_id}/related", limit)

    def genre_tracks(self, genre: str, limit: int = 50, *, min_ms: int = 0,
                     max_ms: int = 0, since: str | None = None) -> list:
        """
        Tracks in one genre, with length and age filtered by SoundCloud.

        Filtering on their side matters: every other read returns mostly
        songs, which the set rule then throws away. `since` is
        'YYYY-MM-DD HH:MM:SS'.
        """
        params = {"genres": genre}
        if min_ms:
            params["duration[from]"] = min_ms
        if max_ms:
            params["duration[to]"] = max_ms
        if since:
            params["created_at[from]"] = since
        return self._first_page("/tracks", limit, params)

    # -- the Discovery playlist -------------------------------------------

    def find_discovery_playlist(self) -> dict | None:
        """The playlist named 'discovery', or None if it does not exist yet."""
        for playlist in self._paginate(
            "/me/playlists", {"show_tracks": "false", "limit": 50}
        ):
            if playlist.get("title", "").lower() == DISCOVERY_PLAYLIST_TITLE:
                return playlist
        return None

    def create_discovery_playlist(self) -> dict:
        return self._request(
            "POST", "/playlists",
            json={"playlist": {"title": DISCOVERY_PLAYLIST_TITLE,
                               "sharing": "private", "tracks": []}},
        )

    def discovery_playlist(self) -> tuple:
        """
        (playlist_id, [track_id, ...]) for Discovery, creating it if needed.

        Ids come back as strings, matching how they are stored, so a caller
        never has to remember which side of the app uses ints.
        """
        playlist = self.find_discovery_playlist() or self.create_discovery_playlist()
        playlist_id = int(playlist["id"])
        detail = self._request("GET", f"/playlists/{playlist_id}")
        return playlist_id, [str(t["id"]) for t in detail.get("tracks", [])]

    def set_playlist_tracks(self, playlist_id: int | str,
                            track_ids: Iterable[str]) -> None:
        """
        Replace a playlist's contents outright.

        SoundCloud has no add or remove endpoint - the full list is PUT every
        time. So the caller must send the tracks it wants to KEEP plus any it
        is adding; anything omitted is removed.
        """
        urns = [{"urn": f"soundcloud:tracks:{tid}"} for tid in track_ids]
        self._request(
            "PUT", f"/playlists/{playlist_id}",
            json={"playlist": {"tracks": urns}},
            headers={"Content-Type": "application/json"},
        )
