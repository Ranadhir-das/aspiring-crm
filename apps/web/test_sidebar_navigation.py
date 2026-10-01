from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()

class SidebarNavigationTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username='admin_nav',
            password='Password123!',
            email='admin_nav@example.com',
            role='ADMIN'
        )
        self.manager = User.objects.create_user(
            username='manager_nav',
            password='Password123!',
            email='manager_nav@example.com',
            role='MANAGER'
        )
        self.caller = User.objects.create_user(
            username='caller_nav',
            password='Password123!',
            email='caller_nav@example.com',
            role='CALLER'
        )

    def test_admin_sidebar_navigation(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('web:dashboard'))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        # Check search input & quick access pills
        self.assertIn('id="nav-search-input"', content)
        self.assertIn('id="quick-pills"', content)

        # Check 10 primary groups
        self.assertIn('data-group-id="overview"', content)
        self.assertIn('data-group-id="leads"', content)
        self.assertIn('data-group-id="calls"', content)
        self.assertIn('data-group-id="admissions"', content)
        self.assertIn('data-group-id="people"', content)
        self.assertIn('data-group-id="performance"', content)
        self.assertIn('data-group-id="communication"', content)
        self.assertIn('data-group-id="workplace"', content)
        self.assertIn('data-group-id="finance"', content)
        self.assertIn('data-group-id="admin-settings"', content)

        # Check admin-only features
        self.assertIn(reverse('web:lead-routing'), content)
        self.assertIn(reverse('web:website-integrations'), content)
        self.assertIn(reverse('web:lead-import'), content)
        self.assertIn(reverse('web:services'), content)
        self.assertIn(reverse('web:activity-log'), content)

    def test_caller_sidebar_navigation(self):
        self.client.force_login(self.caller)
        resp = self.client.get(reverse('web:dashboard'))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        # Caller can see permitted sections
        self.assertIn('data-group-id="overview"', content)
        self.assertIn('data-group-id="leads"', content)
        self.assertIn('data-group-id="calls"', content)
        self.assertIn('data-group-id="admissions"', content)
        self.assertIn('data-group-id="communication"', content)
        self.assertIn('data-group-id="workplace"', content)

        # Permitted items for caller
        self.assertIn(reverse('web:leads'), content)
        self.assertIn(reverse('web:calls'), content)
        self.assertIn(reverse('web:call-recordings'), content)
        self.assertIn(reverse('web:followups'), content)
        self.assertIn(reverse('web:admissions'), content)
        self.assertIn(reverse('web:caller-detail', args=[self.caller.pk]), content)
        self.assertIn(reverse('web:chat-home'), content)
        self.assertIn(reverse('web:notices'), content)
        self.assertIn(reverse('web:attendance'), content)
        self.assertIn(reverse('web:reports'), content)

        # Caller must NOT see management / finance admin sections
        self.assertNotIn('data-group-id="people"', content)
        self.assertNotIn(reverse('web:lead-routing'), content)
        self.assertNotIn(reverse('web:website-integrations'), content)
        self.assertNotIn(reverse('web:lead-import'), content)
        self.assertNotIn(reverse('web:activity-log'), content)
        self.assertNotIn(reverse('web:finance-overview'), content)
        self.assertNotIn(reverse('web:expenses'), content)
        self.assertNotIn(reverse('web:invoices'), content)

    def test_manager_sidebar_navigation(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('web:dashboard'))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        # Manager can see management features
        self.assertIn('data-group-id="people"', content)
        self.assertIn(reverse('web:lead-routing'), content)
        self.assertIn(reverse('web:team'), content)
        self.assertIn(reverse('web:employees'), content)
        self.assertIn(reverse('web:consultations'), content)

        # Manager does not see SUPER_ADMIN/ADMIN only activity-log
        self.assertNotIn(reverse('web:activity-log'), content)
