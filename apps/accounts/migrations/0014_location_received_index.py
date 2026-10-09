from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # Existing history can be large. Keep writes available while PostgreSQL builds
    # the receipt-time index; do not scan all historical rows on every live refresh.
    atomic = False
    dependencies = [('accounts', '0013_location_diagnostics')]
    operations = [AddIndexConcurrently(
        model_name='employeelocationpoint',
        index=models.Index(fields=['employee', '-received_at'], name='employee_location_received'),
    )]
