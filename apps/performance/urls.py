from django.urls import path
from .api import (
    AdjustmentView,
    CallerPointsView,
    CallerProgressView,
    MilestoneView,
    PeerAppreciationStatusView,
    PeerAppreciationView,
)

urlpatterns = [
    path('me/', CallerPointsView.as_view(), name='points-me'),
    path('callers/<int:caller_id>/', CallerPointsView.as_view(), name='points-caller'),
    path('progress/', CallerProgressView.as_view(), name='points-progress'),
    path('adjustments/', AdjustmentView.as_view(), name='points-adjustment'),
    path('milestones/', MilestoneView.as_view(), name='points-milestone'),
    path('peer-appreciation/status/', PeerAppreciationStatusView.as_view(), name='peer-appreciation-status'),
    path('peer-appreciation/', PeerAppreciationView.as_view(), name='peer-appreciation'),
]

