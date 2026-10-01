# Phase 10B: new website-lead push

Only the new-Lead branch of `create_website_lead` registers an `on_commit` callback.
Duplicate enquiries keep their audit records and never register this callback.
Manual creation, CSV/XLSX import and assignment do not call this sender.

Service: `apps/accounts/push_notifications.py` (reuses the existing accounts app).
It re-reads the lead after commit using the shared unclaimed queue predicate in
`apps/leads/claiming.py`. The available-lead endpoint and notification service share
active CALLER/service eligibility queries. There is no new claim/assignment path,
and no changes to claim, release, call-started, outcome or stale-release operations.

Devices must be active and owned by active CALLER users mapped to the active lead
service. All active devices are included; tokens are deduplicated and sent in
batches of at most 100. The lead's availability is rechecked before every batch.
A claim after that check can still race with delivery; taps refresh availability.

Payload:

```json
{
  "to": "ExponentPushToken[device-token]",
  "title": "New Lead Available",
  "body": "Rahul Sharma • Apostille lead is available",
  "data": {"type": "NEW_WEBSITE_LEAD", "lead_id": 123, "service": "APOSTILLE"},
  "channelId": "default",
  "sound": "default"
}
```

Service display text uses Service.name. Name/service display text is length-limited.
No phone, email, notes or credentials are included. API credentials stay server-side.

## Delivery and receipts

`WEBSITE_LEAD_PUSH_ENABLED` defaults to true; set false to disable delivery without
disabling intake. If Expo enhanced push security is enabled for the project, set
`EXPO_PUSH_ACCESS_TOKEN` in the backend environment/secret store. Never put it in
an EXPO_PUBLIC variable or mobile code. Existing Expo/FCM credentials are reused.

Delivery is best-effort and synchronous **after commit**, with a five-second HTTP
timeout per batch. Failures are caught and logged with lead/device IDs or counts;
raw Expo responses, tokens and payloads are not logged. Lead creation remains
successful. There is no durable send outbox, automatic resend, or guaranteed
delivery. This avoids retrying an ambiguously accepted send and duplicating alerts.
Large recipient sets add after-commit request latency; monitor batch failure logs.

An Expo ticket is acceptance, not proof of delivery. Migration
`accounts/0009_pushreceipt.py` persists ticket IDs and the device's registration
timestamp. Schedule the following with your existing task scheduler every 15 minutes:

```text
python manage.py check_push_receipts
```

The command checks up to 1000 tickets aged at least 15 minutes, retaining missing
receipts for another check. Tickets older than 24 hours are discarded with a warning.
Schedule more frequent runs if traffic exceeds 1000 tickets per interval. The command
does not send notifications. This task does not install a system scheduler job.

`DeviceNotRegistered` from a ticket or receipt marks the device inactive without
deleting it. A newer registration timestamp protects a re-registered device from
stale receipts. InvalidCredentials and payload/service errors do not deactivate
devices. Re-registration through the existing endpoint reactivates the device.

## Mobile behavior

Foreground pushes show a banner/list notification, with no automatic navigation.
A valid NEW_WEBSITE_LEAD tap from an authenticated, onboarded caller opens the
existing Available Leads tab, refreshes the queue and places the target first.
If it has disappeared, the screen reports Lead Unavailable. Claim/Call still uses
the existing explicit button and atomic endpoint (including its existing 409 UI).
Invalid payloads and other types keep existing logging behavior. Login is unchanged;
a last native response can be handled after authentication/navigation readiness.
Handled response identifiers are deduplicated, and listeners retain session cleanup.

## Deployment and checks

Apply migrations before enabling delivery on a running backend, restart the backend,
and configure the receipt scheduler. No live pushes are sent by the automated tests.
Rebuild/update mobile JS through the existing deployment workflow. Verify foreground,
background and cold-start taps on the real device; server tickets alone do not
prove visible delivery. A real-device Phase 10B test has not been performed here.

References: https://docs.expo.dev/push-notifications/sending-notifications/
and https://docs.expo.dev/versions/v57.0.0/sdk/notifications/
