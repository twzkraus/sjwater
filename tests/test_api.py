from __future__ import annotations

from datetime import datetime
import json
import logging
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from custom_components.sjwater.api import (
    CannotConnect,
    InvalidAuth,
    SJWaterHubApiClient,
)

from tests.conftest import load_fixture, load_json_fixture, _make_async_resp


class TestAsyncLogin:
    async def test_success(self, api_client, mock_session, mock_get_factory, mock_post_factory):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=load_json_fixture("login_success.json"))

        result = await api_client.async_login()

        assert result is True
        assert api_client._token == "new_session_token_987"
        assert api_client._account_guid == "1234567890"

        get_call = mock_session.get.call_args
        assert get_call is not None
        assert "sjwaterhub.com/Login" in str(get_call[0][0])

        # First POST registers the anti-forgery token, second POST logs in
        assert mock_session.post.call_count == 2
        cep_call = mock_session.post.call_args_list[0]
        assert "CreateExceptionPermissions" in str(cep_call[0][0])
        assert cep_call[1]["data"]["token"] == "abc123def456token789"

        post_call = mock_session.post.call_args
        assert post_call is not None
        assert "RequestBroker" in str(post_call[0][0])
        headers = post_call[1].get("headers", {})
        assert headers.get("X-Requested-With") == "XMLHttpRequest"
        assert "application/json" in headers.get("Content-Type", "")
        assert headers.get("User-Agent", "").startswith("Mozilla/5.0")

    async def test_missing_token_in_html(self, api_client, mock_session, mock_get_factory):
        mock_get_factory(text=load_fixture("login_page_no_token.html"))

        with pytest.raises(CannotConnect, match="expected security token"):
            await api_client.async_login()
        assert api_client._token is None

    async def test_unsuccessful_api_response(self, api_client, mock_session, mock_get_factory, mock_post_factory):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=load_json_fixture("login_failure.json"))

        result = await api_client.async_login()

        assert result is False
        assert api_client._token is None
        assert api_client._account_guid is None

    async def test_account_guid_discovery(self, api_client, mock_session, mock_get_factory, mock_post_factory):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=load_json_fixture("login_success.json"))

        await api_client.async_login()

        assert api_client._account_guid == "1234567890"

    async def test_no_account_guid_in_response(self, api_client, mock_session, mock_get_factory, mock_post_factory):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=load_json_fixture("login_success_no_acct.json"))

        with pytest.raises(CannotConnect, match="numeric account identifier"):
            await api_client.async_login()
        assert api_client._token is None

    async def test_discovers_numeric_value_under_opaque_attribute_keys(
        self, api_client, mock_get_factory, mock_post_factory
    ):
        response = load_json_fixture("login_success.json")
        response["VXengage_Login"]["Transaction"]["Attributes"] = {
            "1825149148": "987654321",
            "1970351119": "Account Name",
        }
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=response)

        assert await api_client.async_login() is True
        assert api_client._account_guid == "987654321"

    async def test_does_not_log_opaque_attribute_keys(
        self, api_client, mock_get_factory, mock_post_factory, caplog
    ):
        response = load_json_fixture("login_success.json")
        response["VXengage_Login"]["Transaction"]["Attributes"] = {
            "1825149148": "987654321",
            "1970351119": "Account Name",
        }
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=response)

        with caplog.at_level(logging.DEBUG, logger="custom_components.sjwater.api"):
            await api_client.async_login()

        assert "1825149148" not in caplog.text
        assert "1970351119" not in caplog.text
        assert "987654321" not in caplog.text

    async def test_login_page_get_uses_browser_headers(self, api_client, mock_session, mock_get_factory, mock_post_factory):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=load_json_fixture("login_success.json"))

        await api_client.async_login()

        get_headers = mock_session.get.call_args[1].get("headers", {})
        assert get_headers.get("User-Agent", "").startswith("Mozilla/5.0")
        assert mock_session.get.call_args[1]["timeout"].total == 30
        assert mock_session.get.call_args[1]["allow_redirects"] is False

    async def test_login_exception_clears_previous_session(
        self, api_client, mock_session
    ):
        api_client._token = "stale-token"
        api_client._account_guid = "1234567890"
        response = _make_async_resp()
        response.raise_for_status.side_effect = aiohttp.ClientError("Connection failed")
        mock_session.get.return_value = response

        with pytest.raises(aiohttp.ClientError):
            await api_client.async_login()

        assert api_client._token is None
        assert api_client._account_guid is None

    async def test_token_registered_before_login(self, api_client, mock_session, mock_get_factory, mock_post_factory):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=load_json_fixture("login_success.json"))

        await api_client.async_login()

        urls = [str(c[0][0]) for c in mock_session.post.call_args_list]
        assert "CreateExceptionPermissions" in urls[0]
        assert "RequestBroker" in urls[1]

    async def test_login_debug_log_excludes_account_attributes(
        self, api_client, mock_get_factory, mock_post_factory, caplog
    ):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=load_json_fixture("login_success.json"))

        with caplog.at_level(logging.DEBUG, logger="custom_components.sjwater.api"):
            await api_client.async_login()

        assert "1234567890" not in caplog.text
        assert "Test Account" not in caplog.text
        assert "test@example.com" not in caplog.text
        assert "success=True" in caplog.text


class TestAsyncVerifyCredentials:
    async def test_success(self, api_client, mock_session, mock_get_factory, mock_post_factory):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=load_json_fixture("login_success.json"))

        result = await api_client.async_verify_credentials()

        assert result is True

    async def test_raises_invalid_auth(self, api_client, mock_session, mock_get_factory, mock_post_factory):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_post_factory(data=load_json_fixture("login_failure.json"))

        with pytest.raises(InvalidAuth):
            await api_client.async_verify_credentials()

    async def test_raises_cannot_connect_on_http_error(self, api_client, mock_session):
        resp = _make_async_resp()
        resp.raise_for_status.side_effect = aiohttp.ClientError("Connection failed")
        mock_session.get.return_value = resp

        with pytest.raises(CannotConnect, match="Cannot reach SJ Water Hub"):
            await api_client.async_verify_credentials()

    async def test_unexpected_error_is_not_masked(self, api_client, mock_session):
        resp = _make_async_resp()
        resp.raise_for_status.side_effect = Exception("Unexpected")
        mock_session.get.return_value = resp

        with pytest.raises(Exception, match="Unexpected"):
            await api_client.async_verify_credentials()


class TestAsyncGetData:
    async def test_successful_fetch(self, authenticated_client, mock_session, mock_post_factory):
        mock_post_factory(data=load_json_fixture("hourly_graph_success.json"))

        result = await authenticated_client.async_get_data()

        assert "gallons" in result
        assert "timestamp" in result
        assert "last_updated" in result
        assert "history" in result
        assert len(result["history"]) > 0
        for entry in result["history"]:
            assert "start" in entry
            assert "state" in entry

    async def test_empty_history(self, authenticated_client, mock_session, mock_post_factory):
        mock_post_factory(data=load_json_fixture("hourly_graph_empty.json"))

        result = await authenticated_client.async_get_data()

        assert result["gallons"] == 0.0
        assert result["history"] == []

    async def test_session_expiry_reauthentication(self, authenticated_client, mock_session, mock_get_factory):
        hourly_expired = load_json_fixture("hourly_graph_expired.json")
        login_ok = load_json_fixture("login_success.json")
        hourly_ok = load_json_fixture("hourly_graph_success.json")

        mock_get_factory(text=load_fixture("login_page.html"))

        call_count: list[int] = [0]

        def post_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_async_resp(data=hourly_expired)
            elif call_count[0] == 2:
                # CreateExceptionPermissions token registration
                return _make_async_resp(data={})
            elif call_count[0] == 3:
                return _make_async_resp(data=login_ok)
            else:
                return _make_async_resp(data=hourly_ok)

        mock_session.post.side_effect = post_side_effect

        result = await authenticated_client.async_get_data()

        assert len(result["history"]) > 0

    async def test_no_account_guid_raises_error(self, api_client, mock_session):
        api_client._token = "some_token"

        with pytest.raises(CannotConnect, match="Account identifier could not be discovered"):
            await api_client.async_get_data()

    async def test_token_rotated_on_data_response(self, authenticated_client, mock_session, mock_post_factory):
        mock_post_factory(data=load_json_fixture("hourly_graph_success.json"))

        await authenticated_client.async_get_data()

        assert authenticated_client._token == "rotated_token_456"


class TestPostRequest:
    async def test_double_encoded_json_format(self, api_client, mock_session, mock_post_factory):
        api_client._token = "test_token"
        mock_post_factory(data={"status": "ok"})

        await api_client._post_request("TestAction", "TestView")

        call_args = mock_session.post.call_args
        assert call_args is not None

        url = str(call_args[0][0])
        assert "RequestBroker" in url

        outer = json.loads(call_args[1].get("data"))
        assert "Request" in outer

        inner = json.loads(outer["Request"])
        assert inner["Actions"] == "TestAction"
        assert inner["ViewName"] == "TestView"
        assert inner["Token"] == "test_token"
        assert "IsMobile" not in inner

        headers = call_args[1].get("headers", {})
        assert headers.get("X-Requested-With") == "XMLHttpRequest"
        assert "application/json" in headers.get("Content-Type", "")
        assert headers.get("User-Agent", "").startswith("Mozilla/5.0")
        assert call_args[1]["allow_redirects"] is False
        assert call_args[1]["timeout"].total == 30
        assert headers.get("Origin") == "https://www.sjwaterhub.com"

    async def test_include_payload_additions(self, api_client, mock_session, mock_post_factory):
        api_client._token = "token"
        mock_post_factory(data={"status": "ok"})

        await api_client._post_request("Action", "View", {"ExtraKey": "ExtraVal"})

        call_args = mock_session.post.call_args
        inner = json.loads(json.loads(call_args[1].get("data"))["Request"])
        assert inner["ExtraKey"] == "ExtraVal"

    async def test_raises_on_http_error(self, api_client, mock_session):
        resp = _make_async_resp(status=400)
        resp.raise_for_status.side_effect = aiohttp.ClientError("HTTP 400")
        mock_session.post.return_value = resp

        with pytest.raises(aiohttp.ClientError):
            await api_client._post_request("Action", "View")

    async def test_raises_on_500(self, api_client, mock_session):
        resp = _make_async_resp(status=500)
        resp.raise_for_status.side_effect = aiohttp.ClientError("HTTP 500")
        mock_session.post.return_value = resp

        with pytest.raises(aiohttp.ClientError):
            await api_client._post_request("Action", "View")

    async def test_rejects_redirects(self, api_client, mock_session):
        mock_session.post.return_value = _make_async_resp(status=307)

        with pytest.raises(CannotConnect, match="redirected"):
            await api_client._post_request("VXengage_Login", "Login")


class TestAsyncGetDataEdgeCases:
    async def test_reauth_failure_raises(self, authenticated_client, mock_session, mock_get_factory):
        hourly_expired = load_json_fixture("hourly_graph_expired.json")
        login_fail = load_json_fixture("login_failure.json")

        mock_get_factory(text=load_fixture("login_page.html"))

        call_count: list[int] = [0]

        def post_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_async_resp(data=hourly_expired)
            else:
                return _make_async_resp(data=login_fail)

        mock_session.post.side_effect = post_side_effect

        with pytest.raises(InvalidAuth, match="Invalid username or password"):
            await authenticated_client.async_get_data()

    async def test_unsuccessful_graph_response_raises(self, authenticated_client, mock_session, mock_post_factory):
        mock_post_factory(data=load_json_fixture("hourly_graph_unsuccessful.json"))

        with pytest.raises(CannotConnect, match="unsuccessful hourly graph data"):
            await authenticated_client.async_get_data()

    async def test_auto_login_when_no_token(self, api_client, mock_session, mock_get_factory, mock_post_factory):
        mock_get_factory(text=load_fixture("login_page.html"))
        mock_session.post.side_effect = [
            _make_async_resp(data={}),
            _make_async_resp(data=load_json_fixture("login_success.json")),
            _make_async_resp(data=load_json_fixture("hourly_graph_success.json")),
        ]

        result = await api_client.async_get_data()

        assert len(result["history"]) > 0

    async def test_dict_type_labels(self, authenticated_client, mock_session, mock_post_factory):
        mock_post_factory(data=load_json_fixture("hourly_graph_dict_labels.json"))

        result = await authenticated_client.async_get_data()

        assert len(result["history"]) == 2
        for entry in result["history"]:
            assert isinstance(entry["start"], datetime)

    async def test_list_type_labels(self, authenticated_client, mock_session, mock_post_factory):
        mock_post_factory(data=load_json_fixture("hourly_graph_list_labels.json"))

        result = await authenticated_client.async_get_data()

        assert len(result["history"]) == 2
        for entry in result["history"]:
            assert isinstance(entry["start"], datetime)

    async def test_unparseable_dates_are_skipped(self, authenticated_client, mock_session, mock_post_factory):
        mock_post_factory(data=load_json_fixture("hourly_graph_bad_dates.json"))

        result = await authenticated_client.async_get_data()

        assert result["history"] == []
        assert result["timestamp"] is None
        assert result["gallons"] == 0.0

    async def test_duplicate_readings_keep_their_own_timestamps(
        self, authenticated_client, mock_session, mock_post_factory
    ):
        mock_post_factory(data={
            "VXengage_GetHourlyGraph": {
                "Success": True,
                "ComplexSlidingGraphDataSets": [
                    {"data": ["1.0", "2.0", "2.0"]}
                ],
                "ComplexLabels": [
                    "2024-06-06 01:00:00",
                    "2024-06-06 02:00:00",
                    "2024-06-06 03:00:00",
                ],
            }
        })

        result = await authenticated_client.async_get_data()

        assert result["gallons"] == 2.0
        assert result["timestamp"] == result["history"][-1]["start"]
        assert result["timestamp"] != result["history"][-2]["start"]

    async def test_negative_gallons_clamped(self, authenticated_client, mock_session, mock_post_factory):
        mock_post_factory(data=load_json_fixture("hourly_graph_negative.json"))

        result = await authenticated_client.async_get_data()

        for entry in result["history"]:
            assert entry["state"] >= 0.0
