from rest_framework import serializers

from apps.accounts.models import PushDevice


class PushDeviceRegistrationSerializer(serializers.Serializer):
    # Both formats are used by Expo. Reject raw FCM tokens and arbitrary strings.
    expo_push_token = serializers.RegexField(
        r'\A(?:Expo|Exponent)PushToken\[[A-Za-z0-9_-]+\]\Z', max_length=255,
        error_messages={'invalid': 'Enter a valid Expo push token.'},
    )
    platform = serializers.ChoiceField(choices=PushDevice.Platform.choices, default='android')
    device_name = serializers.CharField(max_length=255, allow_blank=True, default='')

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({'detail': 'Only expo_push_token, platform and device_name are accepted.'})
        return super().to_internal_value(data)


class PushDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushDevice
        fields = ['id', 'expo_push_token', 'platform', 'device_name', 'active']
        read_only_fields = fields
