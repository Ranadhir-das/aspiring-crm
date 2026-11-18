"""
Forms for the Vaani Admin Work Tracking & PC Activity module.
"""
from datetime import date
from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.activity.models import DesktopActivityBucket

DATE_WIDGET = forms.DateInput(attrs={'type': 'date', 'class': 'input-date'})


class PCTrackingFilterForm(forms.Form):
    date = forms.DateField(
        required=False,
        widget=DATE_WIDGET,
        label="Date",
    )
    employee = forms.ModelChoiceField(
        required=False,
        queryset=User.objects.none(),
        empty_label="All employees",
        label="Employee",
    )
    department = forms.ChoiceField(
        required=False,
        choices=[('', 'All departments')] + list(User.Role.choices),
        label="Department",
    )
    tracking_state = forms.ChoiceField(
        required=False,
        choices=[
            ('', 'All PC states'),
            ('ACTIVE', 'Active on PC'),
            ('IDLE', 'Idle on PC'),
            ('LOCKED', 'Locked'),
            ('SLEEPING', 'Sleeping'),
            ('PAUSED', 'Paused (On Break)'),
            ('OFFLINE', 'Offline / Stopped'),
            ('NO_DEVICE', 'No Device Registered'),
        ],
        label="PC Tracking State",
    )
    device = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Filter by machine name...', 'class': 'input-text'}),
        label="Device",
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not actor or not getattr(actor, 'is_authenticated', False):
            self.fields['employee'].queryset = User.objects.none()
            return

        if actor.role in [User.Role.SUPER_ADMIN, User.Role.ADMIN]:
            self.fields['employee'].queryset = User.objects.filter(is_active=True).order_by(
                'first_name', 'last_name', 'username'
            )
        elif actor.role == User.Role.MANAGER:
            self.fields['employee'].queryset = User.objects.filter(
                Q(id=actor.id) | Q(manager=actor) | Q(manager__manager=actor),
                is_active=True,
            ).order_by('first_name', 'last_name', 'username')
        else:
            self.fields['employee'].widget = forms.HiddenInput()
            self.fields['employee'].queryset = User.objects.filter(id=actor.id)
            self.fields['employee'].initial = actor


class PCReportDailyFilterForm(forms.Form):
    date = forms.DateField(
        required=False,
        widget=DATE_WIDGET,
        label="Date",
    )
    department = forms.ChoiceField(
        required=False,
        choices=[('', 'All departments')] + list(User.Role.choices),
        label="Department",
    )


class PCReportMonthlyFilterForm(forms.Form):
    year = forms.IntegerField(
        required=False,
        min_value=2024,
        max_value=2035,
        widget=forms.NumberInput(attrs={'class': 'input-text', 'style': 'width: 100px;'}),
        label="Year",
    )
    month = forms.ChoiceField(
        required=False,
        choices=[
            (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
            (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
            (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')
        ],
        label="Month",
    )
    department = forms.ChoiceField(
        required=False,
        choices=[('', 'All departments')] + list(User.Role.choices),
        label="Department",
    )


class PCReportDepartmentFilterForm(forms.Form):
    date_from = forms.DateField(
        required=False,
        widget=DATE_WIDGET,
        label="Date from",
    )
    date_to = forms.DateField(
        required=False,
        widget=DATE_WIDGET,
        label="Date to",
    )


class DeviceRevokeForm(forms.Form):
    reason = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Explain why this desktop workstation is being revoked...',
            'class': 'input-textarea',
            'style': 'width: 100%;',
        }),
        max_length=255,
        label="Revocation Reason",
    )

