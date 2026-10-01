# Mobile push-device registration

This phase stores Expo push tokens only. No notification sending, lead routing,
login/logout changes or mobile app changes are included.

Authentication uses the existing `VerifiedSessionAuthentication` subclass of DRF
TokenAuthentication with `IsAuthenticated`. Send `Authorization: Token <token>`.
Existing verified, unexpired work-session and registration-approval checks remain
in effect. Missing/invalid authentication or expired sessions return 401.

## Endpoints

`POST /api/v1/mobile/push-devices/`, JSON:

```json
{
  "expo_push_token": "ExponentPushToken[example-token]",
  "platform": "android",
  "device_name": "Android Phone"
}
```

Token is required, up to 255 characters; ExpoPushToken[...] and
ExponentPushToken[...] formats are supported. Validation checks syntax, not Expo
delivery validity. Platform is android (default) or ios; device_name is optional,
blank by default, up to 255 characters. Other fields, including user/user_id,
active and timestamps, are rejected with 400. The server supplies request.user.

A new token returns 201. Re-registering the authenticated owner's token returns
200, updates platform/name/last_seen, and sets active=true. Omitted metadata uses
the documented defaults. Both responses contain id, expo_push_token, platform,
device_name and active. No authentication credentials are returned.

A token belonging to any other account returns 409 with a generic conflict
message, including when the device or previous owner is inactive. No automatic
ownership transfer is implemented. A unique database constraint and transactional
row locking protect concurrent registration.

`GET /api/v1/mobile/push-devices/` returns an array of only the authenticated
user's devices, including inactive ones. Query parameters cannot change ownership
scope. It returns the same five fields as registration.

`DELETE /api/v1/mobile/push-devices/<id>/` soft-deactivates an owned device and
returns 204. Repeating it returns 204. Missing devices or another user's devices
return 404. Rows and last_seen history are retained.

## Administration and migration

PushDevice lives in apps.accounts and references the existing custom User.
Migration `accounts/0008_pushdevice.py` creates the table, unique token constraint
and (user, active) index. User deletion cascades to devices.

Django Admin lists user, platform, name, masked token, active state and timestamps.
Existing model permissions apply. Full token is read-only on the detail page;
ownership/token/metadata cannot be edited there, and manual creation is disabled.
Admins with change permission can deactivate a device. No tokens are logged by
the API implementation.

The existing app request format already matches these endpoints. Its next
registration attempt can succeed instead of receiving 404. Restart the backend
if it does not auto-reload, and restart/foreground the signed-in app to retry.

## Limitations for later sending phases

Logout does not deactivate devices automatically in this phase. Reassignment
between accounts is deliberately blocked even after deactivation; any future
account-switch policy must explicitly establish ownership. Rotated tokens are
new rows; retiring obsolete registrations and handling Expo delivery receipts
remain future work. Do not infer delivery authorization from active alone without
considering user/session policy when notification sending is implemented.

Verification:

```text
python manage.py test apps.accounts.test_push_devices --noinput
python manage.py test --noinput
python manage.py check
```
