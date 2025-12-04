import aiohttp
import async_timeout
import time
import logging

_LOGGER = logging.getLogger(__name__)


LOGIN_URL = (
    "https://cma-m.iov.changanauto.com.de/"
    "cma-app-gw/cma-app-auth/api/login/email-pass-in/v2"
)


class Mazda6eApi:
    def __init__(self, session: aiohttp.ClientSession, email_enc: str, password_enc: str):
        self._session = session
        self._email_enc = email_enc
        self._password_enc = password_enc
        self._token = None

    async def login(self):
        payload = {
            "email": self._email_enc,
            "password": self._password_enc,

            # PUBLIC KEY AUS DEINEM MITM LOG
            "pubKey": (
                "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCRYk7lZkHwH"
                "CJo8sSoKs5UuD/Jh9j7Pv5Lnoc6wNpVcvGj1LG+a6Kyn+Oo"
                "RSa0NP24MWoLd0WE+zRYJH2RFNdiXHDdHqZYcxtTsvwy"
                "MaBjI6jsizdXrbFc3oBZY4LMfr7nV66/nQB1TP7UO7fYM"
                "ti3/wfHfbFG0BCgCgWeuGeRXQIDAQAB"
            ),

            "loginTime": str(int(time.time()))
        }

        headers = {
            "content-type": "application/json",
            "devicetype": "iPhone",
            "apptype": "IOS",
            "appid": "cma",
            "accept": "*/*",
            "appversion": "V1.1.2",
            "language": "de_DE",
            "deviceid": "HOMEASSISTANT",
            "user-agent": "overseas/1.1.2 (HomeAssistant)",
        }

        _LOGGER.info("Mazda6e: Performing login request")

        async with async_timeout.timeout(20):
            async with self._session.post(
                    LOGIN_URL,
                    json=payload,
                    headers=headers,
            ) as resp:

                text = await resp.text()

                if resp.status != 200:
                    _LOGGER.error("Login failed: %s", text)
                    raise Exception("Mazda 6e Login fehlgeschlagen")

                data = await resp.json()

                # HIER müssen wir gleich noch dein echtes Token-Feld prüfen
                self._token = data.get("accessToken") or data.get("data", {}).get("token")

                _LOGGER.info("Mazda6e: Login successful")

    async def get_vehicle_status(self):
        if not self._token:
            await self.login()

        headers = {
            "Authorization": f"Bearer {self._token}",
            "appid": "cma",
            "apptype": "IOS",
            "devicetype": "iPhone",
        }

        async with async_timeout.timeout(20):
            async with self._session.get(
                    "HIER_KOMMT_DEIN_STATUS_ENDPOINT_REIN",
                    headers=headers,
            ) as resp:
                return await resp.json()
