import json
from django.test import Client, TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.leads.models import Lead


class MarketingWebsiteTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username='admin_marketing',
            email='admin@vaani.com',
            password='TestPassword123!',
            role='ADMIN'
        )

    def test_anonymous_landing_page_rendered_html(self):
        """
        Verify anonymous GET / returns HTTP 200 with complete SaaS marketing HTML:
        title, meta description, canonical, OG tags, Twitter cards, H1, video container, structured data.
        """
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # 1. Title & Meta Description
        self.assertIn('<title>Vaani CRM | Lead Management, Calling &amp; Sales CRM</title>', content)
        self.assertIn('name="description" content="Vaani is an all-in-one CRM for lead management, calling, follow-ups, team performance, WhatsApp communication and admissions."', content)

        # 2. Canonical URL
        self.assertIn('rel="canonical"', content)

        # 3. Open Graph & Twitter Cards
        self.assertIn('property="og:title"', content)
        self.assertIn('property="og:description"', content)
        self.assertIn('property="og:image"', content)
        self.assertIn('name="twitter:card" content="summary_large_image"', content)

        # 4. Semantic H1 & Hero Copy
        self.assertIn('Turn Every Lead', content)
        self.assertIn('Into an Opportunity.', content)
        self.assertIn('THE SMARTER WAY TO MANAGE LEADS', content)
        self.assertIn('Vaani brings lead management, calling, follow-ups, team performance and conversion tracking into one powerful platform.', content)

        # 5. Calls to Action
        self.assertIn('Book a Free Demo', content)
        self.assertIn('Explore Vaani', content)
        self.assertIn('href="/login/"', content)

        # 6. 39-second Video Container
        self.assertIn('id="vaani-video"', content)
        self.assertIn('vaani-intro.mp4', content)
        self.assertIn('video-poster.svg', content)
        self.assertIn('39s Product Overview', content)

        # 7. Problem / Solution Section
        self.assertIn('Why Conventional Lead Management Breaks Down', content)
        self.assertIn('Vaani Brings Everything Together', content)

        # 8. All 8 Feature Cards
        self.assertIn('1. Lead Management', content)
        self.assertIn('2. Smart Calling', content)
        self.assertIn('3. Follow-up Management', content)
        self.assertIn('4. WhatsApp Communication', content)
        self.assertIn('5. Team Performance', content)
        self.assertIn('6. Counselling &amp; Admissions', content)
        self.assertIn('7. Analytics &amp; Reports', content)
        self.assertIn('8. Notifications &amp; Alerts', content)

        # 9. How Vaani Works Workflow
        self.assertIn('Capture Leads', content)
        self.assertIn('Assign to Team', content)
        self.assertIn('Call &amp; Follow Up', content)
        self.assertIn('Track Performance', content)
        self.assertIn('Convert', content)

        # 10. Product Showcase & Performance Metrics
        self.assertIn('CRM Dashboard', content)
        self.assertIn('Android Caller App', content)
        self.assertIn('Illustrative platform workflow capability metrics', content)

        # 11. Who is Vaani for?
        self.assertIn('Education Consultants', content)
        self.assertIn('Admission Teams', content)
        self.assertIn('Counselling Teams', content)
        self.assertIn('Sales Teams', content)
        self.assertIn('Lead-Driven Businesses', content)

        # 12. Book Demo Section
        self.assertIn('Request a Demo', content)
        self.assertIn('id="demo-form"', content)

        # 13. FAQ Accordion (8 questions)
        self.assertIn('What is Vaani?', content)
        self.assertIn('Who is Vaani for?', content)
        self.assertIn('Can Vaani manage callers?', content)
        self.assertIn('Can Vaani track follow-ups?', content)
        self.assertIn('Can Vaani track team performance?', content)
        self.assertIn('Does Vaani support mobile calling?', content)
        self.assertIn('Can I book a demo?', content)
        self.assertIn('How do I get started?', content)

        # 14. Final CTA & Footer
        self.assertIn('Ready to turn more leads into conversions?', content)
        self.assertIn('&copy; 2026 Vaani. All rights reserved.', content)

        # 15. JSON-LD Structured Data
        self.assertIn('"@type": "Organization"', content)
        self.assertIn('"@type": "SoftwareApplication"', content)
        self.assertIn('"@type": "FAQPage"', content)

    def test_anonymous_product_app_page(self):
        """
        Verify GET /app/ returns HTTP 200 with product marketing content and APK information.
        """
        response = self.client.get('/app/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertIn('Your Leads. Your Team.', content)
        self.assertIn('One Powerful Platform.', content)
        self.assertIn('Vaani CRM', content)
        self.assertIn('Vaani Caller App', content)
        self.assertIn('1. Real-Time CRM Dashboard', content)
        self.assertIn('2. Intelligent Lead Management', content)
        self.assertIn('3. Native Android Caller App', content)
        self.assertIn('4. Automated Calling &amp; Call History', content)
        self.assertIn('5. Scheduled Follow-ups &amp; Push Alerts', content)
        self.assertIn('6. 1-Click WhatsApp Communication', content)
        self.assertIn('7. Points Engine &amp; Peer Appreciation', content)
        self.assertIn('8. Real-Time Conversion Analytics', content)
        self.assertIn('9. Counselling &amp; Admissions Pipeline', content)
        self.assertIn('10. Full Shift On Your Phone', content)
        self.assertIn('Request a Demo', content)

    def test_book_demo_valid_json_submission(self):
        """
        Verify anonymous user can submit a valid demo booking request via JSON.
        """
        payload = {
            'name': 'Priya Sharma',
            'company': 'Apex Education Partners',
            'phone': '+91 9876543210',
            'email': 'priya@apexedu.com',
            'employees': '11-50',
            'message': 'We manage 25 student counsellors and need automated callback alerts.'
        }
        response = self.client.post(
            reverse('web:book-demo'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('received', data['message'])

        # Verify record in database
        lead = Lead.objects.filter(email='priya@apexedu.com').first()
        self.assertIsNotNone(lead)
        self.assertEqual(lead.name, 'Priya Sharma')
        self.assertEqual(lead.source, 'vaani_demo_request')
        self.assertEqual(lead.campaign, 'SaaS Website Demo Booking')
        self.assertIn('Apex Education Partners', lead.notes)
        self.assertIn('11-50', lead.notes)

    def test_book_demo_missing_fields_validation(self):
        """
        Verify required fields validation: name, company, phone, email, employees.
        """
        payload = {
            'name': '',
            'company': '',
            'phone': '',
            'email': '',
            'employees': '',
        }
        response = self.client.post(
            reverse('web:book-demo'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('name', data['errors'])
        self.assertIn('company', data['errors'])
        self.assertIn('phone', data['errors'])
        self.assertIn('email', data['errors'])
        self.assertIn('employees', data['errors'])

    def test_book_demo_invalid_email_validation(self):
        """
        Verify email format validation rejects malformed emails.
        """
        payload = {
            'name': 'Aarav Patel',
            'company': 'Global Admissions',
            'phone': '9876543210',
            'email': 'invalid-email-address',
            'employees': '1-10',
        }
        response = self.client.post(
            reverse('web:book-demo'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn('email', data['errors'])

    def test_book_demo_invalid_phone_validation(self):
        """
        Verify phone validation requires at least 8 digits.
        """
        payload = {
            'name': 'Aarav Patel',
            'company': 'Global Admissions',
            'phone': '123',
            'email': 'aarav@global.com',
            'employees': '1-10',
        }
        response = self.client.post(
            reverse('web:book-demo'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn('phone', data['errors'])

    def test_book_demo_honeypot_spam_trap(self):
        """
        Verify bots filling the honeypot field are safely trapped without saving a Lead.
        """
        payload = {
            'name': 'Spam Bot',
            'company': 'Spam Co',
            'phone': '9999999999',
            'email': 'bot@spam.com',
            'employees': '1-10',
            'website_url': 'http://buy-viagra-now.com',
        }
        count_before = Lead.objects.count()
        response = self.client.post(
            reverse('web:book-demo'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Lead.objects.count(), count_before)

    def test_robots_txt_and_sitemap_xml(self):
        """
        Verify robots.txt and sitemap.xml routes return proper headers and content.
        """
        robots_resp = self.client.get('/robots.txt')
        self.assertEqual(robots_resp.status_code, 200)
        self.assertEqual(robots_resp['Content-Type'], 'text/plain')
        self.assertIn('Allow: /', robots_resp.content.decode())
        self.assertIn('Disallow: /admin/', robots_resp.content.decode())
        self.assertIn('Sitemap:', robots_resp.content.decode())

        sitemap_resp = self.client.get('/sitemap.xml')
        self.assertEqual(sitemap_resp.status_code, 200)
        self.assertEqual(sitemap_resp['Content-Type'], 'application/xml')
        self.assertIn('<urlset', sitemap_resp.content.decode())
        self.assertIn('/app/', sitemap_resp.content.decode())

    def test_no_internal_crm_leak_to_anonymous(self):
        """
        Verify anonymous visitors cannot access internal CRM data or lists.
        """
        for internal_url in ['/leads/', '/calls/', '/team/', '/performance/', '/attendance/', '/payroll/']:
            with self.subTest(url=internal_url):
                resp = self.client.get(internal_url)
                # Must either redirect to login (302) or deny (403/401)
                self.assertIn(resp.status_code, [302, 401, 403])
