import struct
import httpx
import re
import secrets
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from server.config import settings
from server.core.database import License, LicenseActivation, BuildVersion

class LicenseActivationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)

class LicenseService:

    FORMAT_VERSION = 1

    @staticmethod
    def _write_string(value: str) -> bytes:
        encoded = value.encode("utf-8")
        return struct.pack(">I", len(encoded)) + encoded
    
    @staticmethod
    def generate_license_key() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        groups = []
        for _ in range(3):
            groups.append("".join(secrets.choice(alphabet) for _ in range(4)))
        return "OBSN-" + "-".join(groups)

    @staticmethod
    def serialize_blob(
        email: str,
        tier: str,
        machine_id: str,
        activation_date_ms: int,
        expiration_date_ms: int,
    ) -> bytes:
        out = struct.pack(">I", LicenseService.FORMAT_VERSION)
        out += LicenseService._write_string(email)
        out += LicenseService._write_string(tier)
        out += LicenseService._write_string(machine_id)
        out += struct.pack(">q", activation_date_ms)
        out += struct.pack(">q", expiration_date_ms)
        return out

    @staticmethod
    def _load_private_key() -> Ed25519PrivateKey:
        raw = bytes.fromhex(settings.LICENSE_SIGNING_PRIVATE_KEY)
        return Ed25519PrivateKey.from_private_bytes(raw)

    @staticmethod
    def sign_blob(blob: bytes) -> bytes:
        return LicenseService._load_private_key().sign(blob)

    @staticmethod
    def create_signed_license(
        email: str,
        tier: str,
        machine_id: str,
        expiration_date_ms: int = 0,
    ) -> dict:
        activation_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        blob = LicenseService.serialize_blob(
            email, tier, machine_id, activation_ms, expiration_date_ms
        )
        signature = LicenseService.sign_blob(blob)
        import base64
        return {
            "blob": base64.b64encode(blob).decode("ascii"),
            "signature": base64.b64encode(signature).decode("ascii"),
        }
    

    @staticmethod
    def activate(db, license_key: str, machine_id: str) -> dict:
        if not machine_id or len(machine_id) != 64:
            raise LicenseActivationError("Invalid machine identifier.")

        license_obj = (
            db.query(License).filter(License.license_key == license_key).first()
        )

        if not license_obj:
            raise LicenseActivationError("License key not found.")

        if license_obj.status != "active":
            raise LicenseActivationError("This license is not active.")

        expiration_ms = 0
        if license_obj.expiration_date is not None:
            if license_obj.expiration_date < datetime.utcnow():
                raise LicenseActivationError("This license has expired.")
            expiration_ms = int(
                license_obj.expiration_date.replace(tzinfo=timezone.utc).timestamp()
                * 1000
            )

        existing = (
            db.query(LicenseActivation)
            .filter(
                LicenseActivation.license_id == license_obj.id,
                LicenseActivation.machine_id == machine_id,
            )
            .first()
        )

        if existing:
            existing.last_seen_at = datetime.utcnow()
            db.commit()
        else:
            current_count = (
                db.query(LicenseActivation)
                .filter(LicenseActivation.license_id == license_obj.id)
                .count()
            )

            if current_count >= license_obj.max_activations:
                raise LicenseActivationError(
                    f"Activation limit reached ({license_obj.max_activations} machines). "
                    "Please deactivate another machine first."
                )

            new_activation = LicenseActivation(
                license_id=license_obj.id,
                machine_id=machine_id,
                activated_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
            )
            db.add(new_activation)
            db.commit()

        return LicenseService.create_signed_license(
            email=license_obj.email,
            tier=license_obj.tier,
            machine_id=machine_id,
            expiration_date_ms=expiration_ms,
        )

    @staticmethod
    def release(db, license_key: str, machine_id: str) -> bool:
        license_obj = (
            db.query(License).filter(License.license_key == license_key).first()
        )

        if not license_obj:
            return False

        activation = (
            db.query(LicenseActivation)
            .filter(
                LicenseActivation.license_id == license_obj.id,
                LicenseActivation.machine_id == machine_id,
            )
            .first()
        )

        if not activation:
            return False

        db.delete(activation)
        db.commit()
        return True
    
    @staticmethod
    async def resolve_github_asset(platform: str) -> tuple[str | None, dict, dict]:
        platform_markers = {
            "windows": ["win64", ".exe"],
            "macos": ["darwin", ".pkg"],
            "linux": ["linux", ".tar.gz"],
        }
        markers = platform_markers[platform]
        headers = {
            "Authorization": f"Bearer {settings.GITHUB_RELEASE_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        api_url = f"https://api.github.com/repos/{settings.GITHUB_COMMERCIAL_REPO}/releases/latest"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(api_url, headers=headers)
            if resp.status_code != 200:
                return None, {}, {}

            release = resp.json()
            for asset in release.get("assets", []):
                name = asset.get("name", "").lower()
                if any(marker.lower() in name for marker in markers):
                    url = await LicenseService.get_asset_download_url(client, asset, headers)
                    return url, release, asset

        return None, {}, {}


    @staticmethod
    async def get_asset_download_url(client, asset, headers) -> str | None:
        asset_api_url = asset.get("url")
        if not asset_api_url:
            return None

        octet_headers = {**headers, "Accept": "application/octet-stream"}
        resp = await client.get(asset_api_url, headers=octet_headers, follow_redirects=False)

        if resp.status_code in (301, 302, 307):
            return resp.headers.get("location")

        return None
    
    @staticmethod
    def parse_version_from_tag(tag: str) -> str:
        return tag.lstrip("v") if tag else "unknown"

    @staticmethod
    def upsert_build_version(db: Session, platform: str, release: dict, asset: dict):


        version = LicenseService.parse_version_from_tag(release.get("tag_name", ""))
        asset_name = asset.get("name", "")

        published_at_raw = release.get("published_at")
        released_at = (
            datetime.fromisoformat(published_at_raw.replace("Z", "+00:00"))
            if published_at_raw else None
        )

        row = db.query(BuildVersion).filter(BuildVersion.platform == platform).first()
        if row:
            row.version = version
            row.asset_name = asset_name
            row.released_at = released_at
            row.updated_at = datetime.utcnow()
        else:
            row = BuildVersion(
                platform=platform,
                version=version,
                asset_name=asset_name,
                released_at=released_at,
            )
            db.add(row)

        db.commit()