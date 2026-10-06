from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def employee_link(context, employee):
    from apps.web.employee_links import employee_link_html
    request = context.get('request')
    return employee_link_html(request.user if request else None, employee)


@register.filter
def user_name(user):
    return (user.get_full_name() or user.username) if user else 'Unassigned'


@register.filter
def duration(value):
    seconds = max(0, int(value or 0))
    return f'{seconds // 3600}h {(seconds % 3600) // 60}m {seconds % 60}s'
