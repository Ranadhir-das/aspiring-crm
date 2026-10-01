from django.urls import path

from .mobile_views import (
    MobileChatAttachmentDownloadView,
    MobileChatChannelListView,
    MobileChatMessageView,
)

urlpatterns = [
    path('chat/channels/', MobileChatChannelListView.as_view(), name='mobile-chat-channels'),
    path('chat/channels/<int:channel_id>/messages/', MobileChatMessageView.as_view(), name='mobile-chat-messages'),
    path('chat/attachments/<int:pk>/download/', MobileChatAttachmentDownloadView.as_view(), name='mobile-chat-attachment-download'),
]
