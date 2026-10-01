from django.contrib import admin
from .models import ChatChannel, ChatMessage, ChatAttachment


class ChatAttachmentInline(admin.TabularInline):
    model = ChatAttachment
    extra = 0
    readonly_fields = ('file', 'original_name', 'mime_type', 'file_size', 'created_at')


@admin.register(ChatChannel)
class ChatChannelAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'description', 'created_at')
    list_filter = ('kind', 'created_at')
    search_fields = ('name', 'description')


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'channel', 'sender', 'text_preview', 'created_at')
    list_filter = ('channel', 'created_at')
    search_fields = ('sender__username', 'text')
    inlines = [ChatAttachmentInline]

    @admin.display(description='Text')
    def text_preview(self, obj):
        return (obj.text[:50] + '...') if len(obj.text) > 50 else obj.text


@admin.register(ChatAttachment)
class ChatAttachmentAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'message', 'mime_type', 'file_size', 'created_at')
    list_filter = ('mime_type', 'created_at')
    search_fields = ('original_name', 'message__sender__username')

