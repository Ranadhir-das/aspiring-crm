"""
Forms for Branch Management and Employee-to-Branch Assignment.
"""
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.activity.models import OfficeLocation


class BranchForm(forms.ModelForm):
    latitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=Decimal("-90.000000"),
        max_value=Decimal("90.000000"),
        widget=forms.NumberInput(attrs={
            "step": "0.000001",
            "placeholder": "e.g. 22.572646",
            "class": "form-input",
            "id": "id_latitude",
        }),
        help_text="Latitude in decimal degrees (-90.000000 to +90.000000)",
    )
    longitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=Decimal("-180.000000"),
        max_value=Decimal("180.000000"),
        widget=forms.NumberInput(attrs={
            "step": "0.000001",
            "placeholder": "e.g. 88.363895",
            "class": "form-input",
            "id": "id_longitude",
        }),
        help_text="Longitude in decimal degrees (-180.000000 to +180.000000)",
    )

    class Meta:
        model = OfficeLocation
        fields = ["name", "code", "address", "latitude", "longitude", "notes", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={
                "placeholder": "e.g. Kolkata Headquarters / DGSME Branch",
                "class": "form-input",
            }),
            "code": forms.TextInput(attrs={
                "placeholder": "e.g. KOL_HQ / DGSME",
                "class": "form-input uppercase-input",
                "style": "text-transform: uppercase;",
            }),
            "address": forms.Textarea(attrs={
                "placeholder": "Street address, building, floor, city, pin code...",
                "rows": 3,
                "class": "form-input",
            }),
            "notes": forms.Textarea(attrs={
                "placeholder": "Optional internal operations notes or landmarks...",
                "rows": 3,
                "class": "form-input",
            }),
            "is_active": forms.CheckboxInput(attrs={
                "class": "form-checkbox",
            }),
        }

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise ValidationError("Branch name is required.")
        return name

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip().upper()
        if not code:
            raise ValidationError("Branch code is required.")
        qs = OfficeLocation.objects.filter(code=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(f"A branch with code '{code}' already exists.")
        return code


class EmployeeBranchAssignmentForm(forms.Form):
    primary_branch = forms.ModelChoiceField(
        queryset=OfficeLocation.objects.filter(is_active=True).order_by("name"),
        required=True,
        empty_label="— Select Primary Branch (Required) —",
        widget=forms.Select(attrs={"class": "form-input", "id": "id_primary_branch"}),
        help_text="The employee's main assigned workplace.",
    )
    additional_branches = forms.ModelMultipleChoiceField(
        queryset=OfficeLocation.objects.filter(is_active=True).order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "branch-checkbox"}),
        help_text="Optional secondary locations where this employee is authorized to work.",
    )
    shift_workflow_enabled = forms.BooleanField(
        required=False,
        label="Explicit Shift Workflow",
        help_text="When enabled, attendance does not automatically start the work session. The employee must explicitly tap Start Shift. Enforces the 8-hour net-work requirement before ending a shift.",
        widget=forms.CheckboxInput(attrs={"class": "form-checkbox", "id": "id_shift_workflow_enabled"}),
    )
    shift_geofence_enforced = forms.BooleanField(
        required=False,
        label="Office Geofence Enforcement",
        help_text="When enabled, enforces office geofence rules for shift start, resume work, and shift end actions. The employee must be within the 50m permitted office perimeter with <=25m accuracy and <=60s freshness. Requires Explicit Shift Workflow to be enabled.",
        widget=forms.CheckboxInput(attrs={"class": "form-checkbox", "id": "id_shift_geofence_enforced"}),
    )

    def __init__(self, *args, user=None, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_user = user
        self.actor = actor

        is_admin = False
        if actor and (getattr(actor, "is_superuser", False) or getattr(actor, "role", "") in {"SUPER_ADMIN", "ADMIN"}):
            is_admin = True
        elif actor is None:
            # If actor is not passed (e.g. legacy test calls), allow fields by default
            is_admin = True

        if not is_admin:
            self.fields.pop("shift_workflow_enabled", None)
            self.fields.pop("shift_geofence_enforced", None)

    def clean(self):
        cleaned_data = super().clean()
        primary = cleaned_data.get("primary_branch")
        additional = cleaned_data.get("additional_branches") or []

        if primary and primary in additional:
            raise ValidationError("A branch cannot be selected as both Primary and Additional.")

        shift_wf = cleaned_data.get("shift_workflow_enabled")
        shift_geo = cleaned_data.get("shift_geofence_enforced")

        if shift_geo and not shift_wf:
            self.add_error(
                "shift_geofence_enforced",
                "Office Geofence Enforcement requires Explicit Shift Workflow to be enabled.",
            )

        return cleaned_data
