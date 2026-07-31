import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server.core.database import SessionLocal, Provider
from server.core.security import encrypt_server_key, decrypt_server_key
from server.services.provider_service import ProviderService

SEP = "─" * 68


def _safe_decrypt(value):
    if not value:
        return None
    try:
        return decrypt_server_key(value)
    except Exception as e:
        return f"<decryption error: {e}>"


def print_provider(p, api_key=None, server_key=None):
    if api_key is None:
        api_key = _safe_decrypt(p.encoded_api_key) or "<missing>"
    if server_key is None:
        server_key = _safe_decrypt(p.encoded_server_auth_key) or "<missing>"

    flags = []
    if p.is_banned:
        flags.append("BANNED")
    if not p.is_active:
        flags.append("inactive")
    if not p.activation_token_used:
        flags.append("not activated")
    suffix = f"  [{', '.join(flags)}]" if flags else ""

    print(SEP)
    print(f"Provider #{p.id}  ·  {p.name}{suffix}")
    print(f"  URL             : {p.url}")
    print(f"  API key         : {api_key}")
    print(f"  Server auth key : {server_key}")


def list_providers(db):
    providers = db.query(Provider).order_by(Provider.id).all()
    if not providers:
        print("No providers in database.")
        return
    print(f"{'ID':>4}  {'NAME':<28} {'ACTIVE':<6} {'BAN':<5} {'ACTIVATED':<9} URL")
    for p in providers:
        print(
            f"{p.id:>4}  {p.name[:28]:<28} "
            f"{str(bool(p.is_active)):<6} {str(bool(p.is_banned)):<5} "
            f"{str(bool(p.activation_token_used)):<9} {p.url}"
        )


def select_providers(db, args):
    q = db.query(Provider)
    if args.id:
        q = q.filter(Provider.id.in_(args.id))
    elif args.name:
        q = q.filter(Provider.name == args.name)
    elif args.all:
        pass
    else:
        return None
    return q.order_by(Provider.id).all()


def rotate(db, providers, rotate_server_key=True):
    for p in providers:
        new_api_key = ProviderService.generate_api_key()
        p.api_key = hashlib.sha256(new_api_key.encode()).hexdigest()
        p.encoded_api_key = encrypt_server_key(new_api_key)

        new_server_key = None
        if rotate_server_key:
            new_server_key = ProviderService.generate_api_key()
            p.encoded_server_auth_key = encrypt_server_key(new_server_key)

        db.flush()
        print_provider(p, api_key=new_api_key, server_key=new_server_key)

    db.commit()
    print(SEP)
    print(f"✅ {len(providers)} provider(s) updated.")
    print(
        "⚠️  Restart the affected containers with the new keys, "
        "otherwise they will be banned on the next ping."
    )


def main():
    parser = argparse.ArgumentParser(description="Rotate / display provider API keys.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="list providers")
    action.add_argument("--show", action="store_true", help="show current keys")
    action.add_argument("--rotate", action="store_true", help="generate new keys")

    target = parser.add_mutually_exclusive_group()
    target.add_argument("--id", type=int, nargs="+", help="one or more IDs")
    target.add_argument("--name", type=str, help="exact provider name")
    target.add_argument("--all", action="store_true", help="all providers")

    parser.add_argument(
        "--no-server-key",
        action="store_true",
        help="do not regenerate the server auth key (API key only)",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="skip confirmation")

    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            list_providers(db)
            return

        providers = select_providers(db, args)
        if providers is None:
            parser.error("specify a target: --id, --name or --all")
        if not providers:
            print("No matching providers.")
            return

        if args.show:
            for p in providers:
                print_provider(p)
            print(SEP)
            return

        print("Affected providers:")
        for p in providers:
            print(f"  #{p.id} {p.name} ({p.url})")
        if not args.yes:
            answer = input(
                f"\nRegenerate keys for {len(providers)} provider(s)? "
                "Current keys will be invalidated. [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Cancelled.")
                return
        print()
        rotate(db, providers, rotate_server_key=not args.no_server_key)

    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
