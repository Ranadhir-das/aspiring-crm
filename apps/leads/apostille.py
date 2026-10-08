import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction


class ApostilleQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if kwargs in ({'created_by': None}, {'created_by_id': None}):
            return super().update(**kwargs)
        raise ValidationError('Save Apostille records individually so their points are reconciled.')

    def bulk_create(self, *args, **kwargs):
        raise ValidationError('Create Apostille records individually so their points are awarded.')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Save Apostille records individually so their points are reconciled.')


class Apostille(models.Model):
    class CandidateType(models.TextChoices):
        EXISTING_LEAD = 'EXISTING_LEAD', 'Existing Lead'
        EXTERNAL = 'EXTERNAL', 'External'

    candidate_type = models.CharField(max_length=20, choices=CandidateType.choices, default=CandidateType.EXTERNAL)
    lead = models.ForeignKey('leads.Lead', null=True, blank=True, on_delete=models.PROTECT, related_name='apostilles')
    name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    number_of_documents = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    country = models.CharField(max_length=100)
    amount_received = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    caller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='caller_apostilles', limit_choices_to={'role': 'CALLER'})
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='apostilles_recorded')
    # Stable identity remains in event keys/reasons even after the business record is deleted.
    ledger_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = ApostilleQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(number_of_documents__gte=1), name='apostille_positive_documents'),
            models.CheckConstraint(condition=models.Q(amount_received__gte=0), name='apostille_nonnegative_amount'),
            models.CheckConstraint(condition=~models.Q(country=''), name='apostille_country_required'),
            models.CheckConstraint(condition=(
                models.Q(candidate_type='EXTERNAL', lead__isnull=True) & ~models.Q(name='') & ~models.Q(phone='')
            ) | models.Q(candidate_type='EXISTING_LEAD', lead__isnull=False, name='', phone=''), name='apostille_candidate_consistent'),
        ]

    @property
    def candidate_name(self):
        return self.lead.name if self.lead_id else self.name

    @property
    def candidate_phone(self):
        return self.lead.phone if self.lead_id else self.phone

    @property
    def points(self):
        from apps.performance.apostille import calculate_apostille_points
        return calculate_apostille_points(self.amount_received)

    def clean_fields(self, exclude=None):
        # IntegerField normally coerces a Python float to int; reject fractional documents.
        if 'number_of_documents' not in (exclude or ()) and self.number_of_documents is not None:
            try:
                value = Decimal(str(self.number_of_documents))
                if not value.is_finite() or value != value.to_integral_value():
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                raise ValidationError({'number_of_documents': 'Enter a positive whole number.'})
        super().clean_fields(exclude=exclude)

    def clean(self):
        from apps.accounts.models import User
        from apps.leads.utils import normalize_phone
        self.name = (self.name or '').strip()
        self.phone = (self.phone or '').strip()
        self.country = (self.country or '').strip()
        errors = {}
        if not self.country:
            errors['country'] = 'Country is required.'
        if self.caller_id and not User.objects.filter(pk=self.caller_id, role='CALLER').exists():
            errors['caller'] = 'Select a caller account.'
        if self.candidate_type == self.CandidateType.EXTERNAL:
            if self.lead_id:
                errors['lead'] = 'External records cannot also reference a lead.'
            if not self.name:
                errors['name'] = 'Name is required for an external candidate.'
            self.phone = normalize_phone(self.phone)
            if not 7 <= len(self.phone) <= 15:
                errors['phone'] = 'Enter a valid phone number (7–15 digits).'
        elif self.candidate_type == self.CandidateType.EXISTING_LEAD:
            if not self.lead_id:
                errors['lead'] = 'Select an existing lead.'
            if self.name or self.phone:
                errors['candidate_type'] = 'Existing-lead records must use the lead details, without external name or phone.'
            from apps.leads.models import Lead
            lead = Lead.objects.filter(pk=self.lead_id).first()
            if lead and (not lead.name.strip() or not 7 <= len(normalize_phone(lead.phone)) <= 15):
                errors['lead'] = 'The selected lead must have a name and valid phone number.'
        if errors:
            raise ValidationError(errors)

    @transaction.atomic
    def save(self, *args, actor=None, **kwargs):
        from apps.performance.apostille import reconcile_apostille_points
        if not self._state.adding:
            current = type(self).objects.select_for_update().get(pk=self.pk)
            self.ledger_key = current.ledger_key
        self.full_clean()
        super().save(*args, **kwargs)
        # Read persisted values so save(update_fields=...) cannot score unsaved attributes.
        reconcile_apostille_points(type(self).objects.get(pk=self.pk), actor=actor)

    def __str__(self):
        return f'{self.candidate_name} — {self.country}'
