from django.urls import path

from .mobile_views import MobileChatChannelListView, MobileChatMessageView

urlpatterns = [
    path('chat/channels/', MobileChatChannelListView.as_view(), name='mobile-chat-channels'),
    path('chat/channels/<int:channel_id>/messages/', MobileChatMessageView.as_view(), name='mobile-chat-messages'),
]
