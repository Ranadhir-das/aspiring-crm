from django.conf import settings
from django.db import models


class EmployeeLocationPoint(models.Model):
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='location_points')
    session = models.ForeignKey('accounts.CallerSession', on_delete=models.CASCADE, related_name='location_points')
    latitude = models.FloatField()
    longitude = models.FloatField()
    accuracy = models.FloatField()
    altitude = models.FloatField(null=True, blank=True)
    speed = models.FloatField(null=True, blank=True)
    heading = models.FloatField(null=True, blank=True)
    recorded_at = models.DateTimeField(db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=32, blank=True, default='')
    platform = models.CharField(max_length=16, blank=True, default='')
    mocked = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ['recorded_at', 'pk']
        indexes = [models.Index(fields=['employee', '-recorded_at', '-id'], name='employee_location_latest'),
                   models.Index(fields=['employee', '-received_at'], name='employee_location_received')]
        constraints = [
            models.UniqueConstraint(fields=['session', 'recorded_at'], name='unique_session_location_time'),
            models.CheckConstraint(condition=models.Q(latitude__gte=-90, latitude__lte=90), name='employee_latitude_valid'),
            models.CheckConstraint(condition=models.Q(longitude__gte=-180, longitude__lte=180), name='employee_longitude_valid'),
            models.CheckConstraint(condition=models.Q(accuracy__gte=0), name='employee_accuracy_valid'),
        ]
