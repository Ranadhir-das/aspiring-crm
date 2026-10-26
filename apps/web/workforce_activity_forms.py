"""
Forms for the Vaani Workforce Activity tracking module.
"""
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.text import slugify

from apps.accounts.models import User
from apps.activity.models import ActivityCategory, BreakReasonConfig, WorkSession

DATE_WIDGET = forms.DateInput(attrs={'type': 'date', 'class': 'input-date'})


class ActivityFilterForm(forms.Form):
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
    status = forms.ChoiceField(
        required=False,
        choices=[
            ('', 'All statuses'),
            (WorkSession.Status.ACTIVE, 'Active'),
            (WorkSession.Status.PAUSED, 'Paused (On Break)'),
            (WorkSession.Status.COMPLETED, 'Completed'),
            (WorkSession.Status.AUTO_CLOSED, 'Auto Closed'),
        ],
        label="Session status",
    )
    employee = forms.ModelChoiceField(
        required=False,
        queryset=User.objects.none(),
        empty_label="All employees",
        label="Employee",
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not actor or not getattr(actor, 'is_authenticated', False):
            self.fields['employee'].queryset = User.objects.none()
            return

        if actor.role in ['SUPER_ADMIN', 'ADMIN']:
            self.fields['employee'].queryset = User.objects.filter(is_active=True).order_by(
                'first_name', 'last_name', 'username'
            )
        elif actor.role == 'MANAGER':
            self.fields['employee'].queryset = User.objects.filter(
                Q(id=actor.id) | Q(manager=actor) | Q(manager__manager=actor),
                is_active=True,
            ).order_by('first_name', 'last_name', 'username')
        else:
            # Caller, counselor, etc. - can only view their own activity
            self.fields['employee'].widget = forms.HiddenInput()
            self.fields['employee'].queryset = User.objects.filter(id=actor.id)
            self.fields['employee'].initial = actor


class ActivityCategoryForm(forms.ModelForm):
    class Meta:
        model = ActivityCategory
        fields = [
            'name',
            'slug',
            'icon_key',
            'color_hex',
            'sort_order',
            'is_active',
            'is_default_for_caller',
            'is_default_for_counselor',
            'is_default_general',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Outbound Calling'}),
            'slug': forms.TextInput(attrs={'placeholder': 'Auto-generated if empty'}),
            'icon_key': forms.TextInput(attrs={'placeholder': 'e.g. phone, briefcase, message-square'}),
            'color_hex': forms.TextInput(attrs={'type': 'text', 'placeholder': '#005DF2'}),
            'sort_order': forms.NumberInput(attrs={'min': 0}),
        }
        help_texts = {
            'slug': 'Unique URL-friendly slug. Auto-derived from name if left empty.',
            'icon_key': 'Mobile icon identifier (e.g. phone, briefcase, message-square, headphones).',
            'color_hex': 'Hex color code (e.g. #005DF2) used in the mobile app and CRM timeline.',
        }

    def clean_slug(self):
        slug = self.cleaned_data.get('slug', '').strip()
        if not slug:
            name = self.cleaned_data.get('name', '').strip()
            slug = slugify(name)
        else:
            slug = slugify(slug)
        return slug

    def clean(self):
        cleaned_data = super().clean()
        is_active = cleaned_data.get('is_active', False)
        is_caller = cleaned_data.get('is_default_for_caller', False)
        is_counselor = cleaned_data.get('is_default_for_counselor', False)
        is_general = cleaned_data.get('is_default_general', False)

        if not is_active and (is_caller or is_counselor or is_general):
            raise ValidationError("An inactive category cannot be configured as a default.")
        return cleaned_data


class BreakReasonConfigForm(forms.ModelForm):
    class Meta:
        model = BreakReasonConfig
        fields = [
            'name',
            'code',
            'warning_threshold_minutes',
            'requires_note',
            'sort_order',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Lunch Break'}),
            'code': forms.TextInput(attrs={'placeholder': 'e.g. LUNCH'}),
            'warning_threshold_minutes': forms.NumberInput(attrs={'min': 1, 'max': 1440}),
            'sort_order': forms.NumberInput(attrs={'min': 0}),
        }
        help_texts = {
            'code': 'Unique uppercase code (e.g. LUNCH, TEA, PERSONAL).',
            'warning_threshold_minutes': 'Minutes before the mobile app flags this break as exceeding expectations.',
            'requires_note': 'Whether the employee must enter a note when taking this break.',
        }

    def clean_code(self):
        code = self.cleaned_data.get('code', '').strip().upper()
        if not code:
            raise ValidationError("Break reason code is required.")
        return code


class AdminCloseSessionForm(forms.Form):
    admin_note = forms.CharField(
        min_length=10,
        max_length=500,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Enter administrative reason for closing this session (minimum 10 characters)...',
        }),
        label="Administrative Closure Note",
        help_text="Required. Must be at least 10 characters.",
    )

