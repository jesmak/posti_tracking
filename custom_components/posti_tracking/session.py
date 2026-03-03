import json
import logging
import re
import urllib
import hashlib
import base64
import secrets
import html
import time
from typing import Optional
from urllib.parse import urlparse, urljoin

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

    def __init__(self, username: str, password: str, timeout=20, stored_tokens: Optional[dict] = None):
        self._username = username
        self._password = password
        self._timeout = timeout
        self._tokens = stored_tokens if stored_tokens else None

    def get_tokens(self) -> Optional[dict]:
        """Get current tokens for persistence"""
        return self._tokens

    def set_tokens(self, tokens: dict) -> None:
        """Set tokens from persistent storage"""
        self._tokens = tokens

    def _decode_jwt_payload(self, token: str) -> Optional[dict]:
        """Decode JWT token payload without verification"""
        try:
            # Split token and get payload (second part)
            parts = token.split('.')
            if len(parts) != 3:
                return None
            
            # Add padding if needed
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding
            
            # Decode base64
            decoded = base64.urlsafe_b64decode(payload)
            return json.loads(decoded)
        except Exception as e:
            _LOGGER.debug(f"Failed to decode JWT: {e}")
            return None

    def _is_token_expired(self, buffer_seconds: int = 300) -> bool:
        """Check if access token is expired or will expire soon"""
        if not self._tokens or 'id_token' not in self._tokens:
            return True
        
        payload = self._decode_jwt_payload(self._tokens['id_token'])
        if not payload or 'exp' not in payload:
            return True
        
        # Check if token expires within buffer_seconds (default 5 minutes)
        expiry_time = payload['exp']
        current_time = time.time()
        
        return (expiry_time - current_time) < buffer_seconds

    def refresh_tokens(self) -> None:
        """Refresh access token using refresh token"""
        try:
            if not self._tokens or 'refresh_token' not in self._tokens:
                _LOGGER.debug("No refresh token available, performing full authentication")
                self.authenticate()
                return

            _LOGGER.debug("Refreshing access token using refresh token")
            
            response = requests.post(
                url=f"{AUTH_SERVICE_BASE_URL}/token_v2",
                headers={
                    "User-Agent": "okhttp/4.12.0",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=self._timeout,
                data=f"refresh_token={self._tokens['refresh_token']}&grant_type=refresh_token"
            )

            if response.status_code == 200:
                new_tokens = response.json()
                if 'error' not in new_tokens:
                    # Update tokens while preserving refresh_token if not provided
                    if 'refresh_token' not in new_tokens and 'refresh_token' in self._tokens:
                        new_tokens['refresh_token'] = self._tokens['refresh_token']
                    self._tokens = new_tokens
                    _LOGGER.debug("Successfully refreshed access token")
                    return
            
            # If refresh failed, do full authentication
            _LOGGER.debug(f"Token refresh failed with status {response.status_code}, performing full authentication")
            self.authenticate()

        except Exception as exception:
            _LOGGER.warning(f"Token refresh failed: {exception}, performing full authentication")
            self.authenticate()

    def authenticate(self) -> None:
        try:
            session = requests.Session()

            # Generate PKCE code verifier and challenge
            code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
            code_challenge = base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode('utf-8')).digest()
            ).decode('utf-8').rstrip('=')

            # Initial login request with PKCE
            response = session.get(
                url=f"{AUTH_SERVICE_BASE_URL}/login",
                params={
                    "redirect_uri": "https://oma.posti.fi/app/login",
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                    "redirect": "false",
                    "locale": "fi",
                    "mobile": "true"
                },
                headers={
                    "User-Agent": "okhttp/4.12.0"
                },
                timeout=self._timeout,
            )

            login_data = response.json()
            login_url = login_data.get("login_url")

            # Follow the login URL
            response = session.get(
                url=login_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "x-posti-mobile": "android",
                    "X-Requested-With": "fi.itella.posti.android"
                },
                timeout=self._timeout,
            )

            # Extract session ID from the redirected URL
            session_id = re.search('_id=(.+?)(?:$|&)', response.url).group(1)

            # Submit credentials with mobile authentication method
            response = session.post(
                url=f"{UAS_BASE_URL}/authn/{session_id}/submit",
                params={
                    "entityID": "34aaf9ea-e060-4d9d-b9a2-2cc6a0e44a2a",
                    "locale": "fi"
                },
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "fi.itella.posti.android"
                },
                timeout=self._timeout,
                data=f"username={urllib.parse.quote(self._username)}&password={urllib.parse.quote(self._password)}&method=passwordsql"
            )

            # Extract authorization code from the response
            code = None
            if response.history:
                for resp in response.history:
                    location = resp.headers.get('Location', '')
                    if 'auth-service.posti.fi/api/v1/oidc_callback' in location:
                        code_match = re.search('code=([^&]+)', location)
                        if code_match:
                            code = code_match.group(1)
                            break
            
            # If code not found in history, check final response URL
            if not code:
                code_match = re.search('code=([^&]+)', response.url)
                if code_match:
                    code = code_match.group(1)
            
            # If still no code, look for auto-submit form (success page)
            if not code and 'success.jsp' in response.text:
                form_match = re.search(r'<form[^>]+action=["\']([^"\']+)["\'][^>]*>(.*?)</form>', response.text, re.DOTALL | re.IGNORECASE)
                if form_match:
                    form_content = form_match.group(2)
                    
                    # Extract the code and state from hidden input fields
                    code_input = re.search(r'<input[^>]+name=["\']code["\'][^>]+value=["\']([^"\']+)["\']', form_content, re.IGNORECASE)
                    state_input = re.search(r'<input[^>]+name=["\']state["\'][^>]+value=["\']([^"\']+)["\']', form_content, re.IGNORECASE)
                    
                    if code_input:
                        code = html.unescape(code_input.group(1))
                        
                        # Visit the oidc_callback URL like the mobile app does
                        if state_input:
                            state = html.unescape(state_input.group(1))
                            form_action = html.unescape(form_match.group(1))
                            callback_url = f"{form_action}?code={code}&state={state}"
                            session.get(callback_url, timeout=self._timeout, allow_redirects=True)
            
            if not code:
                raise PostiException("Failed to extract authorization code")

            # Exchange code for tokens with PKCE verifier
            token_data = f"code={code}&code_verifier={code_verifier}&grant_type=authorization_code"
            
            response = session.post(
                url=f"{AUTH_SERVICE_BASE_URL}/token_v2",
                headers={
                    "User-Agent": "okhttp/4.12.0",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=self._timeout,
                data=token_data
            )

            self._tokens = response.json()
            if 'error' in self._tokens:
                raise PostiException(f"Token exchange failed: {self._tokens['error']}")

        except ConnectTimeout as exception:
            raise PostiException("Timeout error") from exception

        except RequestException as exception:
            raise PostiException(f"Communication error {exception}") from exception

    def call_api(self, data: str, reauthenticated=False) -> Optional[dict]:
        try:
            # Proactively refresh token if expired or expiring soon
            if self._is_token_expired():
                _LOGGER.debug("Token expired or expiring soon, refreshing...")
                self.refresh_tokens()
            
            role = next((x for x in self._tokens['role_tokens'] if x['type'] == 'consumer'), None)

            if role is None:
                raise PostiException("Failed to get consumer role")

            response = requests.post(
                url=GRAPH_API_URL,
                headers={
                    "Authorization": f"Bearer {self._tokens['id_token']}",
                    "Content-Type": "application/json",
                    "X-Omaposti-Roles": role['token']
                },
                json=json.loads(data),
                timeout=self._timeout,
            )

            if response.status_code == 401 and reauthenticated is False:
                _LOGGER.debug("Got 401 response, re-authenticating...")
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
