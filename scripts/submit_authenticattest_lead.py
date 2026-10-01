#!/usr/bin/env python
"""
Realistic AuthenticAttest lead submission test script for Vaani CRM.

Usage:
    # 1. In-process test (works even without dev server running):
    python scripts/submit_authenticattest_lead.py

    # 2. Against running local server (default: http://127.0.0.1:8000):
    python scripts/submit_authenticattest_lead.py --server http://127.0.0.1:8000

    # 3. Test duplicate lead submission:
    python scripts/submit_authenticattest_lead.py --duplicate

    # 4. Dry-run validation only (no DB write):
    python scripts/submit_authenticattest_lead.py --dry-run
"""

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Setup Django environment to automatically retrieve active AuthenticAttest API credentials
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from apps.leads.models import Lead, WebsiteLeadSubmission, WebsiteSource
from rest_framework.test import APIClient


def parse_args():
    parser = argparse.ArgumentParser(description="Submit a realistic AuthenticAttest lead to Vaani CRM.")
    parser.add_argument("--name", default="Dr. Ananya Iyer", help="Applicant name")
    parser.add_argument("--phone", default=None, help="Phone number (default: auto-generates +91 98765 XXXXX)")
    parser.add_argument("--email", default="ananya.iyer@example.com", help="Email address")
    parser.add_argument("--service", default="APOSTILLE", help="Service code (default: APOSTILLE)")
    parser.add_argument("--source", default="authentic_attest", help="Source code (default: authentic_attest)")
    parser.add_argument("--campaign", default="uae-embassy-attestation", help="Campaign tag")
    parser.add_argument("--location", default="Bengaluru, Karnataka", help="City / State")
    parser.add_argument(
        "--notes",
        default="Requires MEA Apostille and UAE Embassy attestation for MBBS degree & transcript.",
        help="Enquiry notes",
    )
    parser.add_argument("--origin", default="https://authenticattest.com", help="Origin header")
    parser.add_argument("--api-key", default=None, help="API key override (default: auto-lookup from DB)")
    parser.add_argument("--server", default=None, help="Target URL (e.g. http://127.0.0.1:8000). If omitted, runs in-process.")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing to database")
    parser.add_argument("--duplicate", action="store_true", help="Submit twice to verify duplicate handling")
    return parser.parse_args()


def main():
    args = parse_args()

    # Look up WebsiteSource and API Key
    source = WebsiteSource.objects.filter(code__iexact=args.source).first()
    api_key = args.api_key
    if not api_key:
        if not source:
            print(f"[ERROR] Source '{args.source}' not found in database. Create it via /website-integrations/new/ first.")
            sys.exit(1)
        api_key = source.api_key

    masked_key = f"{api_key[:6]}********{api_key[-4:]}" if len(api_key) > 10 else "**********"

    phone = args.phone
    if not phone:
        phone = f"+91 98765 {random.randint(10000, 99999)}"

    payload = {
        "name": args.name,
        "phone": phone,
        "email": args.email,
        "service": args.service,
        "source": source.code if source else args.source,
        "campaign": args.campaign,
        "location": args.location,
        "notes": args.notes,
    }
    endpoint_path = "/api/v1/public/leads/"
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
    }
    if args.origin:
        headers["Origin"] = args.origin

    print("\n" + "=" * 62)
    print("  AuthenticAttest Realistic Lead Submission Test")
    print("=" * 62)
    print(f"Source:       {source.name if source else args.source} ({args.source})")
    print(f"API Key:      {masked_key} (from database)")
    print(f"Service:      {payload['service']}")
    print(f"Origin:       {args.origin or 'None (server-to-server)'}")
    print(f"Mode:         {'HTTP (' + args.server + ')' if args.server else 'In-process (DRF APIClient)'}")
    print("Payload:")
    print(json.dumps(payload, indent=2))
    print("-" * 62)

    def send_request(p):
        if args.server:
            url = args.server.rstrip("/") + endpoint_path
            req = urllib.request.Request(url, data=json.dumps(p).encode("utf-8"), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req) as resp:
                    status_code = resp.status
                    body = json.loads(resp.read().decode("utf-8"))
                    return status_code, body
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8")
                try:
                    body = json.loads(err_body)
                except Exception:
                    body = {"detail": err_body}
                return e.code, body
        else:
            client = APIClient()
            resp = client.post(
                endpoint_path,
                p,
                format="json",
                HTTP_X_API_KEY=api_key,
                HTTP_ORIGIN=args.origin if args.origin else None,
            )
            return resp.status_code, resp.data

    import contextlib
    from django.db import transaction
    atomic_ctx = transaction.atomic() if args.dry_run else contextlib.nullcontext()
    with atomic_ctx:
        status_code, response_data = send_request(payload)

        if status_code in (200, 201, 202):
            print(f"[SUCCESS] Submission Accepted (HTTP {status_code})")
        else:
            print(f"[FAILED] Submission Rejected (HTTP {status_code})")

        print(f"Response: {json.dumps(response_data, indent=2)}")

        lead_id = response_data.get("lead_id")
        if lead_id:
            lead = Lead.objects.filter(pk=lead_id).first()
            if lead:
                print("\nCRM Database Verification:")
                print(f"  * Lead ID:          #{lead.pk}")
                print(f"  * Name:             {lead.name}")
                print(f"  * Normalized Phone: {lead.phone}")
                print(f"  * Service Type:     {lead.service_type}")
                print(f"  * Routing Status:   {lead.routing_status}")

                submission = WebsiteLeadSubmission.objects.filter(lead=lead).order_by("-submitted_at").first()
                if submission:
                    print(f"  * Audit Submission: #{submission.pk} (Source: {submission.source}, Duplicate: {submission.is_duplicate})")

                if lead.service_type:
                    eligible_callers = lead.service_type.employees.filter(is_active=True, role="CALLER")
                    caller_names = [c.get_full_name() or c.username for c in eligible_callers]
                    print(f"  * Eligible Callers:  {', '.join(caller_names) if caller_names else 'No callers mapped yet (assign via /services/callers/)'}")

        if args.duplicate:
            print("\n" + "-" * 62)
            print("Submitting Duplicate Enquiry (same phone)...")
            dup_status, dup_resp = send_request(payload)
            print(f"Status:   HTTP {dup_status}")
            print(f"Response: {json.dumps(dup_resp, indent=2)}")
            if dup_resp.get("is_duplicate"):
                print("[SUCCESS] Duplicate detection successfully flagged repeat enquiry.")

        if args.dry_run:
            transaction.set_rollback(True)
            print("\n[DRY RUN] Database transaction rolled back. No lead was persisted.")

    print("=" * 62 + "\n")


if __name__ == "__main__":
    main()
