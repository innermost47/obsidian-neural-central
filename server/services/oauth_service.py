from typing import Dict, Any
import httpx
from urllib.parse import urlencode
from server.config import settings
import logging

logger = logging.getLogger(__name__)


class OAuthService:

    GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

    @staticmethod
    def get_google_authorization_url() -> str:
        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "offline",
            "prompt": "consent",
        }

        url = f"{OAuthService.GOOGLE_AUTHORIZATION_URL}?{urlencode(params)}"
        return url

    @staticmethod
    async def get_google_user_info(code: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient() as client:
                token_response = await client.post(
                    OAuthService.GOOGLE_TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": settings.GOOGLE_CLIENT_ID,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET,
                        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                        "grant_type": "authorization_code",
                    },
                )

                if token_response.status_code != 200:
                    print(f"Token exchange failed: {token_response.text}")
                    raise Exception("Failed to exchange authorization code")

                tokens = token_response.json()
                access_token = tokens.get("access_token")

                userinfo_response = await client.get(
                    OAuthService.GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )

                if userinfo_response.status_code != 200:
                    print(f"User info request failed: {userinfo_response.text}")
                    raise Exception("Failed to get user information")

                user_info = userinfo_response.json()
                return user_info

        except Exception as e:
            print(f"Google OAuth error: {str(e)}")
            raise
