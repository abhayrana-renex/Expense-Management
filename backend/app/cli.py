"""Command line: python -m app.cli <command>."""

import argparse
import json
import sys
from pathlib import Path

from app.core.database import SessionLocal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="Reference data + super admin (idempotent)")
    sub.add_parser("seed-demo", help="Demo masters, users and approval rules (idempotent)")
    sub.add_parser("bill-months", help="Ensure previous/current/next FY bill months exist")
    sub.add_parser("verify-audit", help="Verify the audit log hash chain")
    exp = sub.add_parser("export-openapi", help="Write the OpenAPI contract to a file")
    exp.add_argument("--out", default="../docs/openapi.json")
    args = parser.parse_args(argv)

    if args.command == "export-openapi":
        from app.main import app

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {out}")
        return 0

    from app import seed
    from app.services import audit_service, config_service

    with SessionLocal() as db:
        if args.command == "seed":
            seed.seed_reference(db)
            print("Reference data seeded")
        elif args.command == "seed-demo":
            seed.seed_demo(db)
            print("Demo data seeded")
        elif args.command == "bill-months":
            created = config_service.ensure_bill_months(db)
            db.commit()
            print(f"Created {created} bill months")
        elif args.command == "verify-audit":
            result = audit_service.verify_chain(db)
            print(json.dumps(result))
            return 0 if result["valid"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
