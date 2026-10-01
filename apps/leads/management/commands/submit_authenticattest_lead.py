import json
import random
import urllib.request
import urllib.error

from django.core.management.base import BaseCommand, CommandError
from rest_framework.test import APIClient

from apps.leads.models import Lead, Service, WebsiteLeadSubmission, WebsiteSource


class Command(BaseCommand):
    help = "Submit a realistic AuthenticAttest lead to the public intake API using the configured/generated API key."

    def add_arguments(self, parser):
        parser.add_argument(
            "--name",
            default="Dr. Ananya Iyer",
            help="Applicant name (default: 'Dr. Ananya Iyer')",
        )
        parser.add_argument(
            "--phone",
            default=None,
            help="Phone number (default: generates a fresh valid +91 98765 XXXXX phone)",
        )
        parser.add_argument(
            "--email",
            default="ananya.iyer@example.com",
            help="Applicant email",
        )
        parser.add_argument(
            "--service",
            default="APOSTILLE",
            help="Service code (default: 'APOSTILLE')",
        )
        parser.add_argument(
            "--campaign",
            default="uae-embassy-attestation",
            help="Marketing campaign name",
        )
        parser.add_argument(
            "--location",
            default="Bengaluru, Karnataka",
            help="Applicant city/state",
        )
        parser.add_argument(
            "--notes",
            default="Requires MEA Apostille and UAE Embassy attestation for MBBS degree & transcript.",
            help="Lead notes or requirement details",
        )
        parser.add_argument(
            "--origin",
            default="https://authenticattest.com",
            help="HTTP Origin header (default: 'https://authenticattest.com')",
        )
        parser.add_argument(
            "--api-key",
            default=None,
            help="Override API key (default: automatically retrieves generated key from WebsiteSource DB)",
        )
        parser.add_argument(
            "--source-code",
            default="authentic_attest",
            help="Source code to look up (default: 'authentic_attest')",
        )
        parser.add_argument(
            "--server",
            default=None,
            help="Target live server URL (e.g. 'http://127.0.0.1:8000'). If omitted, runs in-process via DRF APIClient.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Dry-run validation without writing to database",
        )
        parser.add_argument(
            "--duplicate",
            action="store_true",
            help="Submit twice with the same phone to test duplicate enquiry handling",
        )

    def handle(self, *args, **options):
        source_code = options["source_code"].strip()
        source = WebsiteSource.objects.filter(code__iexact=source_code).first()

        api_key = options["api_key"]
        if not api_key:
            if not source:
                raise CommandError(
                    f"Website source '{source_code}' not found in database. "
                    "Create it via /website-integrations/new/ or specify --api-key."
                )
            api_key = source.api_key

        if source and not source.is_active:
            self.stdout.write(self.style.WARNING(f"Warning: Website source '{source.code}' is currently inactive!"))

        # Build payload
        phone = options["phone"]
        if not phone:
            rand_suffix = random.randint(10000, 99999)
            phone = f"+91 98765 {rand_suffix}"

        payload = {
            "name": options["name"],
            "phone": phone,
            "email": options["email"],
            "service": options["service"],
            "source": source.code if source else source_code,
            "campaign": options["campaign"],
            "location": options["location"],
            "notes": options["notes"],
        }

        endpoint_path = "/api/v1/public/leads/"
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
        }
        if options["origin"]:
            headers["Origin"] = options["origin"]

        masked_key = f"{api_key[:6]}********{api_key[-4:]}" if len(api_key) > 10 else "**********"

        self.stdout.write(self.style.MIGRATE_HEADING("\n" + "=" * 62))
        self.stdout.write(self.style.MIGRATE_HEADING("  AuthenticAttest Realistic Lead Submission Test"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 62))
        self.stdout.write(f"Source:       {source.name if source else source_code} ({source_code})")
        self.stdout.write(f"API Key:      {masked_key} (retrieved from database)")
        self.stdout.write(f"Service:      {payload['service']}")
        self.stdout.write(f"Origin:       {options['origin'] or 'None (server-to-server)'}")
        self.stdout.write(f"Mode:         {'Remote HTTP (' + options['server'] + ')' if options['server'] else 'In-process (DRF APIClient)'}")
        self.stdout.write(f"Payload:\n{json.dumps(payload, indent=2)}\n")

        # Submit
        def do_submission(p):
            if options["server"]:
                url = options["server"].rstrip("/") + endpoint_path
                req = urllib.request.Request(url, data=json.dumps(p).encode("utf-8"), headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req) as resp:
                        status_code = resp.status
                        body = json.loads(resp.read().decode("utf-8"))
                        return status_code, body
                except urllib.error.HTTPError as e:
                    status_code = e.code
                    err_body = e.read().decode("utf-8")
                    try:
                        body = json.loads(err_body)
                    except Exception:
                        body = {"detail": err_body}
                    return status_code, body
            else:
                client = APIClient()
                resp = client.post(
                    endpoint_path,
                    p,
                    format="json",
                    HTTP_X_API_KEY=api_key,
                    HTTP_ORIGIN=options["origin"] if options["origin"] else None,
                )
                return resp.status_code, resp.data

        import contextlib
        from django.db import transaction
        atomic_ctx = transaction.atomic() if options["dry_run"] else contextlib.nullcontext()
        with atomic_ctx:
            status_code, response_data = do_submission(payload)

            self.stdout.write(self.style.MIGRATE_HEADING("-" * 62))
            if status_code in (200, 201, 202):
                self.stdout.write(self.style.SUCCESS(f"[SUCCESS] Submission Accepted (HTTP {status_code})"))
            else:
                self.stdout.write(self.style.ERROR(f"[FAILED] Submission Rejected (HTTP {status_code})"))

            self.stdout.write(f"Response: {json.dumps(response_data, indent=2)}")

            # Verification in CRM DB
            lead_id = response_data.get("lead_id")
            if lead_id:
                lead = Lead.objects.filter(pk=lead_id).first()
                if lead:
                    self.stdout.write(self.style.MIGRATE_HEADING("\nCRM Database Verification:"))
                    self.stdout.write(f"  * Lead ID:         #{lead.pk}")
                    self.stdout.write(f"  * Name:            {lead.name}")
                    self.stdout.write(f"  * Normalized Phone:{lead.phone}")
                    self.stdout.write(f"  * Service Type:    {lead.service_type}")
                    self.stdout.write(f"  * Routing Status:  {lead.routing_status}")

                    submission = WebsiteLeadSubmission.objects.filter(lead=lead).order_by("-submitted_at").first()
                    if submission:
                        self.stdout.write(f"  * Audit Submission:#{submission.pk} (Source: {submission.source}, Duplicate: {submission.is_duplicate})")

                    # Caller eligibility check
                    if lead.service_type:
                        eligible_callers = lead.service_type.employees.filter(is_active=True, role="CALLER")
                        caller_names = [c.get_full_name() or c.username for c in eligible_callers]
                        self.stdout.write(f"  * Eligible Callers: {', '.join(caller_names) if caller_names else 'No callers mapped yet (assign via /services/callers/)'}")

            # If duplicate test requested
            if options["duplicate"]:
                self.stdout.write(self.style.MIGRATE_HEADING("\n" + "-" * 62))
                self.stdout.write(self.style.MIGRATE_HEADING("Submitting Duplicate Enquiry (same phone)..."))
                dup_status, dup_resp = do_submission(payload)
                self.stdout.write(f"Status:   HTTP {dup_status}")
                self.stdout.write(f"Response: {json.dumps(dup_resp, indent=2)}")
                if dup_resp.get("is_duplicate"):
                    self.stdout.write(self.style.SUCCESS("[SUCCESS] Duplicate detection successfully flagged repeat enquiry."))

            if options["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("\n[DRY RUN] Database transaction rolled back. No lead was persisted."))

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 62 + "\n"))
