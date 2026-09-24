from django.test.runner import DiscoverRunner


class AppTestRunner(DiscoverRunner):
    """Discover every app's automated tests, excluding root manual smoke scripts.

    Unconverted root test_*.py scripts query a pre-existing live database on import and are not
    unittest tests. Explicit test labels still work unchanged.
    """
    default_test_labels = (
        'apps', 'test_assignment', 'test_assignment_api',
        'test_commit', 'test_import', 'test_mobile_login',
    )

    def build_suite(self, test_labels=None, **kwargs):
        return super().build_suite(test_labels or self.default_test_labels, **kwargs)
