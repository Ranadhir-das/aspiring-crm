from django import template

register = template.Library()


@register.filter
def user_name(user):
    return (user.get_full_name() or user.username) if user else 'Unassigned'
