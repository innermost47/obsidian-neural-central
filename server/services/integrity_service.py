import hashlib
import logging
import asyncio
from typing import Optional
import httpx
from server.core.security import decrypt_server_key

logger = logging.getLogger(__name__)

PROVIDER_GITHUB_URL = "https://raw.githubusercontent.com/innermost47/obsidian-neural-provider/main/provider.py"

_github_file_content: Optional[bytes] = None


def get_github_file_content() -> Optional[bytes]:
    return _github_file_content


def compute_expected_provider_hash(
    github_file_content: bytes,
    provider_api_key_hash: str,
    encoded_server_auth_key: str,
) -> str:
    decrypted_shared_secret = decrypt_server_key(encoded_server_auth_key)
    identity = f"{provider_api_key_hash}:{decrypted_shared_secret}".encode()
    return hashlib.sha256(github_file_content + identity).hexdigest()


def verify_provider_hash(
    x_provider_hash: str,
    provider_api_key_hash: str,
    encoded_server_auth_key: str,
) -> bool:
    content = get_github_file_content()
    if not content:
        print("⚠️  GitHub reference not loaded — skipping")
        return True
    expected = compute_expected_provider_hash(
        content, provider_api_key_hash, encoded_server_auth_key
    )
    return x_provider_hash == expected


async def _fetch_github_content() -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(PROVIDER_GITHUB_URL)
            if r.status_code == 200:
                return r.content
            logger.warning(f"⚠️  GitHub fetch failed: HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"⚠️  GitHub fetch error: {e}")
    return None


async def initialize_provider_hash():
    global _github_file_content
    content = await _fetch_github_content()
    if content:
        _github_file_content = content
        logger.info(
            f"🔒 Provider reference code initialized "
            f"({len(content)} bytes, sha256: {hashlib.sha256(content).hexdigest()[:16]}…)"
        )
    else:
        logger.warning(
            "⚠️  Could not initialize provider reference code — "
            "integrity checks disabled until next refresh"
        )


async def refresh_expected_provider_hash():
    global _github_file_content
    while True:
        await asyncio.sleep(3600)
        content = await _fetch_github_content()
        if content:
            _github_file_content = content
            logger.info(
                f"🔒 Provider reference code refreshed "
                f"({len(content)} bytes, sha256: {hashlib.sha256(content).hexdigest()[:16]}…)"
            )
        else:
            logger.warning("⚠️  GitHub refresh failed — keeping previous reference")
