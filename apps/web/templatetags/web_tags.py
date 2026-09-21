from django import template

register = template.Library()


@register.filter
def user_name(user):
    return (user.get_full_name() or user.username) if user else 'Unassigned'


@register.filter
def duration(value):
    seconds = max(0, int(value or 0))
    return f'{seconds // 3600}h {(seconds % 3600) // 60}m {seconds % 60}s'
