from django.apps import AppConfig


class LeadsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.leads"

    def ready(self):
        try:
            from corsheaders.signals import check_request_enabled

            def cors_check_request_enabled(sender, request, **kwargs):
                if request.path.startswith("/api/v1/public/"):
                    return True
                return False

            check_request_enabled.connect(cors_check_request_enabled)
        except Exception:
            pass