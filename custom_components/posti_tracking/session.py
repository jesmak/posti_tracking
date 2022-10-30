import json
import logging
import re
import urllib
from typing import Optional
from urllib.parse import urlparse

import requests
from requests import ConnectTimeout, RequestException

from .const import AUTH_SERVICE_BASE_URL, UAS_BASE_URL, USER_AGENT, GRAPH_API_URL

_LOGGER = logging.getLogger(__name__)


class PostiException(Exception):
    """Base exception for Posti"""


class PostiSession:
    _username: str
    _password: str
    _timeout: int
    _tokens: any

    def __init__(self, username: str, password: str, timeout=20):
        self._username = username
        self._password = password
        self._timeout = timeout

    def authenticate(self) -> None:
        try:
            session = requests.Session()

            response = session.get(
                url=f"{AUTH_SERVICE_BASE_URL}/login?redirect_uri=https://oma.posti.fi/fi&locale=fi",
                headers={
                    "User-Agent": USER_AGENT
                },
                timeout=self._timeout,
            )

            if response.status_code != 200:
                raise PostiException(f"{response.status_code} is not valid")

            session_id = re.search('_id=(.+?)(?:$|&)', response.url).group(1)

            response = session.post(
                url=f"{UAS_BASE_URL}/authn/{session_id}/submit?entityID=5b05bc63-9195-4687-9ac0-df872a6f936e&locale=fi",
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=self._timeout,
                data=f"username={urllib.parse.quote(self._username)}&password={urllib.parse.quote(self._password)}&method=posti.ldapcustomeragent.1"
            )

            if response.status_code != 200:
                raise PostiException(f"{response.status_code} is not valid")

            code_match = re.search('<input type="hidden" name="code" value="(.*)" />', response.text)
            state_match = re.search('<input type="hidden" name="state" value="(.*)" />', response.text)

            response = session.get(
                url=f"{AUTH_SERVICE_BASE_URL}/oidc_callback?code={code_match.group(1)}&state={state_match.group(1)}",
                headers={
                    "User-Agent": USER_AGENT,
                },
                timeout=self._timeout,
            )

            if response.status_code != 200:
                raise PostiException(f"{response.status_code} is not valid")

            response = session.post(
                url=f"{AUTH_SERVICE_BASE_URL}/token_v2",
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=self._timeout,
                data=f"code={code_match.group(1)}&state={state_match.group(1)}"
            )

            if response.status_code != 200:
                raise PostiException(f"{response.status_code} is not valid")

            self._tokens = response.json()

        except ConnectTimeout as exception:
            raise PostiException("Timeout error") from exception

        except RequestException as exception:
            raise PostiException(f"Communication error {exception}") from exception

    def call_api(self, data: str, reauthenticated=False) -> Optional[dict]:
        try:
            response = requests.post(
                url=GRAPH_API_URL,
                headers={
                    "Authorization": f"Bearer {self._tokens['id_token']}",
                    "Content-Type": "application/json",
                },
                json=json.loads(data),
                timeout=self._timeout,
            )

            if response.status_code == 401 and reauthenticated is False:
                self.authenticate()
                return self.call_api(data, True)  # avoid reauthentication loops by using the reauthenticated flag

            elif response.status_code != 200:
                raise PostiException(f"{response.status_code} is not valid")

            else:
                result = response.json() if response else {}
                return result['data']

        except ConnectTimeout as exception:
            raise PostiException("Timeout error") from exception

        except RequestException as exception:
            raise PostiException(f"Communication error {exception}") from exception
