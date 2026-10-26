import json
import logging
import re

import aiohttp
from dateutil.parser import ParserError, parse as dt_parse

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# The portal silently rejects logins from non-browser clients (Success=false,
# ErrorCode=0), so all requests must present browser-like headers.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class InvalidAuth(HomeAssistantError):
    """Error to indicate authentication failure."""


class CannotConnect(HomeAssistantError):
    """Error to indicate connection failure."""


class SJWaterHubApiClient:
    def __init__(self, username, password, session: aiohttp.ClientSession):
        self.username = username
        self.password = password
        self.session = session
        self.base_url = "https://www.sjwaterhub.com/api/WebApi/RequestBroker"
        self._token = None
        self._account_guid = None

    async def _post_request(self, actions: str, view_name: str, payload_additions: dict = None) -> dict:
        """Helper to send the double-encoded JSON the RequestBroker API expects."""

        # Build the inner JSON payload (mirrors the fields the portal's own
        # requestBroker() JS helper sends -- no extras like IsMobile)
        inner_payload = {
            "Actions": actions,
            "Token": self._token if self._token else "",
            "ViewName": view_name,
            "SitePrefix": ""
        }

        if payload_additions:
            inner_payload.update(payload_additions)

        # Wrap it in the outer Request dict
        outer_payload = {
            "Request": json.dumps(inner_payload)
        }

        # Match the browser's $.ajax call exactly: JSON body, browser headers
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json;charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "User-Agent": BROWSER_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.sjwaterhub.com",
            "Referer": "https://www.sjwaterhub.com/Login",
        }

        _LOGGER.debug("Sending RequestBroker Actions: %s", actions)

        async with self.session.post(
            self.base_url,
            data=json.dumps(outer_payload),
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        ) as response:
            if 300 <= response.status < 400:
                raise CannotConnect("The water portal redirected an API request")
            response.raise_for_status()
            response_json = await response.json()
            return response_json

    async def async_login(self) -> bool:
        """Authenticate and discover the account identifier."""
        self._token = None
        self._account_guid = None
        try:
            success = await self._async_login()
            if not success:
                self._token = None
                self._account_guid = None
            return success
        except Exception:
            self._token = None
            self._account_guid = None
            raise

    async def _async_login(self) -> bool:
        """Perform the portal login after clearing any previous session state."""

        # 1. Provide the necessary GET request to establish initially required cookies
        #    such as 'ApplicationGatewayAffinity' and extract the anti-forgery Token.
        login_url = "https://www.sjwaterhub.com/Login"
        get_headers = {
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with self.session.get(
            login_url,
            headers=get_headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        ) as page_response:
            if 300 <= page_response.status < 400:
                raise CannotConnect("The water portal redirected the login page")
            page_response.raise_for_status()
            html = await page_response.text()

            # 2. Extract Token using regex from the hidden input: id="Token"
            match = re.search(r'id="Token"[^>]*value="([^"]+)"', html)
            if not match:
                raise CannotConnect("The login page did not contain the expected security token")

            self._token = match.group(1)
            _LOGGER.debug("Extracted initial session Token")

        # 2b. Register the token with the server, mirroring createExceptions()
        # in the portal's page-load JS -- login is rejected without this step.
        exc_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.sjwaterhub.com",
            "Referer": "https://www.sjwaterhub.com/Login",
        }
        async with self.session.post(
            "https://www.sjwaterhub.com/api/WebApi/CreateExceptionPermissions",
            data={"token": self._token, "sitePrefix": ""},
            headers=exc_headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        ) as exc_response:
            if 300 <= exc_response.status < 400:
                raise CannotConnect("The water portal redirected token registration")
            exc_response.raise_for_status()
            _LOGGER.debug("CreateExceptionPermissions status: %s", exc_response.status)

        # 3. Use the extracted token to login
        login_additions = {
            "UserName": self.username,
            "Password": self.password,
            "Language": "",
            "ExistingMFAToken": None,
            "RedirectUrl": "",
            "AdditionalParameters": {"TargetElementId": "Login_Password"}
        }

        data = await self._post_request("VXengage_Login", "Login", login_additions)

        if not isinstance(data, dict) or not isinstance(data.get("VXengage_Login"), dict):
            raise CannotConnect("The water portal returned an unexpected login response")

        login_result = data["VXengage_Login"]
        if "Success" not in login_result:
            raise CannotConnect("The water portal returned an incomplete login response")

        _LOGGER.debug(
            "Login result: authorized=%s valid=%s success=%s",
            data.get("Authorized"),
            data.get("Valid"),
            login_result.get("Success"),
        )

        if login_result.get("Success") is not True:
            _LOGGER.error("Login failed")
            return False

        # Update our session token for subsequent data requests!
        if data.get("Token"):
            self._token = data.get("Token")

        # The portal uses opaque attribute keys; the account identifier is
        # stored in one of their numeric values, as in the original integration.
        transaction = login_result.get("Transaction")
        tx_attributes = (
            transaction.get("Attributes") if isinstance(transaction, dict) else None
        )
        account_identifier = None
        if isinstance(tx_attributes, dict):
            for value in tx_attributes.values():
                if isinstance(value, int) and not isinstance(value, bool):
                    value = str(value)
                if isinstance(value, str) and value.isdigit() and len(value) >= 6:
                    account_identifier = value
                    break

        if account_identifier is None:
            raise CannotConnect(
                "The login response did not include a numeric account identifier"
            )

        self._account_guid = account_identifier
        _LOGGER.debug("Discovered account identifier")

        return True

    async def async_verify_credentials(self) -> bool:
        """Verify credentials by attempting a login.

        Raises InvalidAuth on authentication failure or CannotConnect on
        network/connection errors. Returns True on success.
        """
        try:
            success = await self.async_login()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"Cannot reach SJ Water Hub: {err}") from err

        if not success:
            raise InvalidAuth("Invalid username or password")
        return True

    async def async_get_data(self) -> dict:
        """Fetch data from the water company's RequestBroker API."""

        # Ensure we are logged in first
        if not self._token:
            if not await self.async_login():
                raise InvalidAuth("Invalid username or password")

        if not self._account_guid:
            raise CannotConnect("Account identifier could not be discovered from login response")

        # Using the exact payload expected by VXengage_GetHourlyGraph
        data_additions = {
            "AccountNumber": self._account_guid,
            "Template": "HourlyWaterUsage",
            "StartDate": "",
            "EndDate": "",
            "GroupData": True
        }

        data = await self._post_request("VXengage_GetHourlyGraph", "UnderstandUsage", data_additions)

        if data.get("SessionExpired"):
            _LOGGER.debug("Session expired. Re-authenticating...")
            if not await self.async_login():
                raise InvalidAuth("Invalid username or password")
            if not self._account_guid:
                raise CannotConnect("Account identifier could not be discovered from login response")
            data_additions["AccountNumber"] = self._account_guid
            data = await self._post_request("VXengage_GetHourlyGraph", "UnderstandUsage", data_additions)
            if data.get("SessionExpired"):
                raise InvalidAuth("The water portal session expired after reauthentication")

        # Extract the VXengage_GetHourlyGraph inner response
        graph_data = data.get("VXengage_GetHourlyGraph", {})

        if not graph_data or not graph_data.get("Success"):
            _LOGGER.error("Failed to fetch hourly graph data")
            raise CannotConnect("The water portal returned unsuccessful hourly graph data")

        # Update our token if it revolved
        if data.get("Token"):
            self._token = data.get("Token")

        # 3. Parse the returned JSON dict
        # The schema uses ComplexSlidingGraphDataSets, SlidingGraphDataSets, Labels, etc.
        # We need the most recent usage point (gallons) and the timestamp
        datasets = graph_data.get("ComplexSlidingGraphDataSets", [])
        labels = graph_data.get("ComplexLabels", [])

        parsed_history = []

        if datasets:
            gallons_data = datasets[0].get("data", [])
            local_tz = dt_util.DEFAULT_TIME_ZONE
            invalid_timestamp_count = 0

            for idx, value in enumerate(gallons_data):
                if value == "":
                    continue
                try:
                    gallons = max(0.0, float(value))
                except (TypeError, ValueError):
                    _LOGGER.debug("Skipping invalid water reading at index %d", idx)
                    continue

                label_val = labels[idx] if idx < len(labels) else ""
                if isinstance(label_val, dict):
                    label_str = label_val.get("Date", label_val.get("Label", ""))
                elif isinstance(label_val, list):
                    label_str = " ".join(str(part) for part in label_val)
                else:
                    label_str = str(label_val)

                try:
                    dt_naive = dt_parse(label_str)
                except (ParserError, TypeError, ValueError, OverflowError):
                    invalid_timestamp_count += 1
                    continue

                if dt_naive.tzinfo is None:
                    dt_aware = dt_naive.replace(tzinfo=local_tz)
                else:
                    dt_aware = dt_naive

                parsed_history.append({
                    "start": dt_util.as_utc(dt_aware),
                    "state": gallons,
                })

            if invalid_timestamp_count:
                _LOGGER.warning(
                    "Skipped %d water readings with invalid timestamps",
                    invalid_timestamp_count,
                )

        latest_reading = parsed_history[-1] if parsed_history else None

        return {
            "gallons": latest_reading["state"] if latest_reading else 0.0,
            "timestamp": latest_reading["start"] if latest_reading else None,
            "last_updated": graph_data.get("LastUpdated", ""),
            "history": parsed_history,
        }
