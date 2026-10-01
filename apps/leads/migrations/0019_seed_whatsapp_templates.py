from django.db import migrations


def seed_whatsapp_templates(apps, schema_editor):
    WhatsAppTemplate = apps.get_model('leads', 'WhatsAppTemplate')
    defaults = [
        {
            'title': 'Initial Follow-up',
            'message': 'Hello {name}, following up regarding your enquiry with Vaani. When would be a convenient time to speak?',
            'is_active': True,
        },
        {
            'title': 'Interested Lead Follow-up',
            'message': 'Hello {name}, thank you for speaking with us today! Please let us know if you need any further details or guidance regarding the course and admission process.',
            'is_active': True,
        },
        {
            'title': 'Call-back Reminder',
            'message': 'Hello {name}, we tried reaching you earlier regarding your enquiry with Vaani. Please let us know when we can connect again.',
            'is_active': True,
        },
        {
            'title': 'General Information',
            'message': 'Hello {name}, greetings from Vaani! Here is the information regarding our upcoming batches and courses. Please feel free to reply if you have any questions.',
            'is_active': True,
        },
    ]
    for item in defaults:
        WhatsAppTemplate.objects.get_or_create(title=item['title'], defaults=item)


def reverse_seed_whatsapp_templates(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('leads', '0018_whatsapptemplate_whatsappactivity'),
    ]

    operations = [
        migrations.RunPython(seed_whatsapp_templates, reverse_seed_whatsapp_templates),
    ]
