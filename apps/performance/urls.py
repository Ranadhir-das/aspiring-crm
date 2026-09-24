from django.urls import path
from .api import AdjustmentView, CallerPointsView, MilestoneView

urlpatterns = [
    path('me/', CallerPointsView.as_view(), name='points-me'),
    path('callers/<int:caller_id>/', CallerPointsView.as_view(), name='points-caller'),
    path('adjustments/', AdjustmentView.as_view(), name='points-adjustment'),
    path('milestones/', MilestoneView.as_view(), name='points-milestone'),
]
