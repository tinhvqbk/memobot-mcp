"""Unofficial Memobot API client.

Reverse-engineered from app.memobot.io's own network traffic — there is no
public Memobot API documentation. Endpoints and field names may change
without notice.
"""

import httpx

from .auth import get_access_token

BASE_URL = "https://sohoa.memobot.io"


class MemobotClient:
    def __init__(self):
        self._access_token = None

    def _headers(self):
        if self._access_token is None:
            self._access_token = get_access_token()
        return {
            "Authorization": f"Basic {self._access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method, path, params=None):
        url = f"{BASE_URL}{path}"
        response = httpx.request(method, url, params=params, headers=self._headers())
        if response.status_code == 401:
            # Cached token was rejected — try a silent refresh (falls back to
            # a browser login only if the refresh_token is also dead) and retry once.
            self._access_token = get_access_token(invalidate_cache=True)
            response = httpx.request(method, url, params=params, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def get_current_user(self):
        return self._request("GET", "/authen/api/v1/auth/user")

    def list_recordings(self, page=1, limit=10):
        return self._request(
            "GET",
            "/analytic-v2/api/audio",
            params={"page": page, "limit": limit, "sort[create_time]": -1},
        )

    def get_audio_detail(self, audio_id):
        """Raw recording detail, including the transcript (under
        content.document.children — unverified structure, see README) and a
        pre-signed, unauthenticated audio_document.url when present."""
        return self._request(
            "GET", f"/analytic-v2/api/audio/{audio_id}", params={"add_audio_url": "true"}
        )

    def get_recording_summary(self, audio_id):
        """Raw AI-generated summary feed for a recording (unverified structure)."""
        return self._request(
            "GET",
            "/analytic-v2/api/feeds/get-one",
            params={"filter[related.audioId]": audio_id},
        )

    def get_user_info(self):
        return self._request("GET", "/analytic-v2/api/userStats/user-info")

    def get_user_package(self, limit=1000):
        return self._request(
            "GET", "/analytic-v2/api/v1/payment/user-package", params={"limit": limit}
        )

    def get_usage_stats(self):
        return self._request("GET", "/analytic-v2/api/v1/payment/user-usage-stats")

    def get_user_config(self):
        return self._request("GET", "/analytic-v2/api/user-config")

    def get_api_key(self):
        return self._request("GET", "/analytic/api/v1/apikeys")

    def get_notifications(self, max_result=10):
        return self._request(
            "GET",
            "/analytic/v1/notif-current",
            params={"order_by": "create_time", "order_direction": -1, "max_result": max_result},
        )
