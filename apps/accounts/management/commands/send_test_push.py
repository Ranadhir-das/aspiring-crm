from django.core.management.base import BaseCommand, CommandError
from apps.accounts.models import PushDevice, User
from apps.accounts.push_notifications import send_expo_push_notification


class Command(BaseCommand):
    help = 'Send a test push notification to active PushDevice records of a specified user via Expo.'

    def add_arguments(self, parser):
        parser.add_argument(
            'username_pos',
            nargs='?',
            type=str,
            default=None,
            help='Username of the user to receive the test push (positional alternative to --user)',
        )
        parser.add_argument(
            '--user',
            type=str,
            dest='user_opt',
            default=None,
            help='Username of the user to receive the test push notification',
        )
        parser.add_argument(
            '--title',
            type=str,
            default='Vaani Test Notification',
            help='Notification title (default: "Vaani Test Notification")',
        )
        parser.add_argument(
            '--body',
            type=str,
            default='Push notifications are working successfully.',
            help='Notification body (default: "Push notifications are working successfully.")',
        )

    def handle(self, *args, **options):
        username = options.get('user_opt') or options.get('username_pos')
        if not username:
            raise CommandError('Please provide a username using --user <username> (e.g. --user user1)')

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f'User "{username}" does not exist.')

        active_devices = list(PushDevice.objects.filter(user=user, active=True))
        if not active_devices:
            self.stdout.write(self.style.WARNING(f'No active push devices found for user "{user.username}".'))
            inactive_count = PushDevice.objects.filter(user=user, active=False).count()
            if inactive_count > 0:
                self.stdout.write(f'Note: User "{user.username}" has {inactive_count} inactive device(s).')
            return

        title = options['title']
        body = options['body']

        self.stdout.write(f'Found {len(active_devices)} active push device(s) for user "{user.username}".')
        self.stdout.write(f'Notification payload: title="{title}", body="{body}"')

        success_count = 0
        failure_count = 0

        for device in active_devices:
            self.stdout.write(
                f'Sending test push to device #{device.pk} '
                f'({device.device_name or device.platform}, token: {device.masked_token})...'
            )
            result = send_expo_push_notification(
                token=device.expo_push_token,
                title=title,
                body=body,
                data={'type': 'test_push', 'user_id': user.pk, 'device_id': device.pk},
                channel_id='default',
            )

            if result['success']:
                ticket_id = result.get('ticket_id') or 'N/A'
                self.stdout.write(self.style.SUCCESS(
                    f'  [OK] Delivered to Expo: status={result["status"]}, ticket_id={ticket_id}'
                ))
                success_count += 1
            else:
                self.stdout.write(self.style.ERROR(
                    f'  [FAIL] status={result["status"]}, message={result.get("message")}'
                ))
                if result.get('details'):
                    self.stdout.write(self.style.ERROR(f'    details: {result["details"]}'))
                failure_count += 1

        self.stdout.write(f'Result: {success_count} succeeded, {failure_count} failed.')
