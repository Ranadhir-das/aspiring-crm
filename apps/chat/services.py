from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def broadcast_message(message):
    """Pushes a just-saved ChatMessage to every socket currently subscribed to its
    channel. Safe no-op if the channel layer isn't configured for some reason —
    the message is already persisted either way, so nothing is lost, just not live."""
    layer = get_channel_layer()
    if not layer:
        return
    async_to_sync(layer.group_send)(f'chat_{message.channel_id}', {
        'type': 'chat.message',
        'message': message.as_payload(),
    })
