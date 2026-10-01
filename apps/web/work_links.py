from django import forms
from django.core.validators import URLValidator


class WorkLinksWidget(forms.Widget):
    template_name = 'web/work_links_widget.html'

    class Media:
        js = ['web/work-links.js']

    def value_from_datadict(self, data, files, name):
        return data.getlist(name) if hasattr(data, 'getlist') else data.get(name, [])

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['links'] = value or ['']
        return context


class WorkLinksField(forms.Field):
    widget = WorkLinksWidget

    def clean(self, value):
        links = [link.strip() for link in (value or []) if link.strip()]
        if len(links) > 30:
            raise forms.ValidationError('Use at most 30 work links.')
        for link in links:
            if len(link) > 2000:
                raise forms.ValidationError('Each work link must be at most 2000 characters.')
            URLValidator(schemes=['http', 'https'])(link)
        return links
