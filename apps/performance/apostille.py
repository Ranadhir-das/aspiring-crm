from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import PointsEntry
from .services import award


def calculate_apostille_points(amount_received):
    try:
        amount = Decimal(str(amount_received))
        if not amount.is_finite() or amount < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Enter a non-negative amount.')
    for threshold, points in ((100000, 1000), (50000, 500), (40000, 300), (30000, 200),
                              (20000, 100), (10000, 50), (5000, 30), (3000, 20), (1500, 10)):
        if amount >= threshold:
            return points
    return 0


@transaction.atomic
def reconcile_apostille_points(record, *, actor=None, deleting=False):
    """Append signed deltas; never rewrite/delete historical ledger entries."""
    from apps.leads.models import Apostille
    locked = Apostille.objects.select_for_update().filter(pk=record.pk).first()
    if locked is None:
        return
    totals = dict(PointsEntry.objects.filter(apostille=locked).order_by().values('caller_id')
                  .annotate(total=Sum('points')).values_list('caller_id', 'total'))
    desired = {} if deleting else {locked.caller_id: calculate_apostille_points(locked.amount_received)}
    for caller_id in sorted(set(totals) | set(desired)):
        delta = desired.get(caller_id, 0) - totals.get(caller_id, 0)
        if not delta:
            continue
        award(caller_id=caller_id, event=PointsEntry.Event.APOSTILLE, points=delta,
              key=f'apostille:{locked.ledger_key}:{uuid4()}',
              reason=f'Apostille #{locked.pk} ({locked.ledger_key}): {"deletion reversal" if deleting else "point adjustment"}; {delta:+d} points',
              occurred_at=timezone.now(), apostille=None if deleting else locked, lead=locked.lead, recorded_by=actor)
