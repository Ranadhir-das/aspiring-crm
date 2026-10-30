from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm
from apps.accounts.models import User
from apps.leads.models import Lead
from django.db.models import Count, Q

MANAGEMENT = {User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MANAGER}
CALLING = MANAGEMENT | {User.Role.CALLER}
ACCESS = set(User.Role.values)


class LoginForm(AuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username is not None and password:
            self.user_cache = authenticate(
                self.request, username=username, password=password
            )
            if self.user_cache is None:
                user = User.objects.filter(username=username).first()
                if user and user.check_password(password) and not user.is_active:
                    raise forms.ValidationError('This account is inactive.', code='inactive')
                raise self.get_invalid_login_error()
            else:
                self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.role not in ACCESS:
            raise forms.ValidationError('Your account does not have workspace access.')


class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ['name', 'phone', 'email', 'location', 'college', 'neet_status',
                  'pcb_percentage', 'preferred_intake', 'source', 'campaign', 'status', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 4})}

    def clean_phone(self):
        phone = self.cleaned_data['phone'].strip()
        from apps.leads.phone_normalization import normalize_phone_number
        norm, reason = normalize_phone_number(phone)
        if not norm:
            raise forms.ValidationError(reason or 'Enter a valid Indian or Bangladesh mobile number.')
        return norm

    def clean_pcb_percentage(self):
        value = self.cleaned_data.get('pcb_percentage')
        if value is not None and not 0 <= value <= 100:
            raise forms.ValidationError('Enter a percentage between 0 and 100.')
        return value


class FollowUpForm(forms.Form):
    scheduled_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def clean_scheduled_at(self):
        from django.utils import timezone
        value = self.cleaned_data['scheduled_at']
        if value <= timezone.now():
            raise forms.ValidationError('Choose a future date and time.')
        return value


from apps.leads.courses import Course, validate_course


class CallerChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        pending = getattr(obj, 'pending_lead_count', None)
        if pending is None:
            pending = obj.assigned_leads.filter(status=Lead.Status.PENDING).count()
        lead_word = "lead" if pending == 1 else "leads"
        return f"{obj} — {pending} pending {lead_word}"


class ImportForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['caller'].queryset = (
            User.objects.filter(
                Q(role=User.Role.CALLER) | Q(can_act_as_caller=True),
                is_active=True,
            )
            .annotate(
                pending_lead_count=Count(
                    'assigned_leads',
                    filter=Q(assigned_leads__status=Lead.Status.PENDING),
                )
            )
            .order_by('pending_lead_count', 'first_name', 'last_name', 'username')
        )
    import_method = forms.CharField(widget=forms.HiddenInput(), required=False, initial='paste')
    idempotency_key = forms.CharField(widget=forms.HiddenInput(), required=False)
    preferred_course = forms.ChoiceField(choices=[('', 'Select a course'), *Course.choices], widget=forms.RadioSelect)
    preferred_course_custom = forms.CharField(max_length=150, required=False, label='Enter Course Name (Others)')

    pasted_numbers = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 8,
            'placeholder': 'Paste phone numbers here, one per line...\ne.g.\n9392130350\n+91 6304569634\n+880 1712345678\n8801712345678\n01712345678',
            'class': 'paste-numbers-textarea',
        }),
        required=False,
        label='Paste phone numbers',
        help_text='One number per line. Indian (+91) and Bangladesh (+880, 88) mobile numbers are automatically recognized and normalized.',
    )

    caller = CallerChoiceField(widget=forms.Select(attrs={'class': 'caller-select'}),
        queryset=User.objects.filter(role=User.Role.CALLER, is_active=True),
        required=False, label='Assign imported leads to',
        empty_label='Leave unassigned',
        help_text='Only newly imported leads are assigned. Existing duplicates keep their current caller.',
    )
    file = forms.FileField(widget=forms.ClearableFileInput(attrs={'accept': '.csv,.xlsx'}), required=False)

    def clean_file(self):
        value = self.cleaned_data.get('file')
        if not value:
            return None
        if not value.name.lower().endswith(('.csv', '.xlsx')):
            raise forms.ValidationError('Choose a CSV or XLSX file.')
        if value.size > 5 * 1024 * 1024:
            raise forms.ValidationError('Choose a file smaller than 5 MB.')
        return value

    def clean(self):
        data = super().clean()
        if data.get('preferred_course'):
            try:
                data['preferred_course'], data['preferred_course_custom'] = validate_course(
                    data['preferred_course'], data.get('preferred_course_custom'))
            except ValueError as exc:
                self.add_error('preferred_course_custom', str(exc))

        method = data.get('import_method') or 'paste'
        has_file = bool(data.get('file'))
        has_paste = bool(str(data.get('pasted_numbers') or '').strip())

        if method == 'file' and not has_file:
            self.add_error('file', 'Please choose a CSV or Excel file.')
        elif method == 'paste' and not has_paste and not has_file:
            self.add_error('pasted_numbers', 'Please paste phone numbers to import.')
        elif not has_file and not has_paste:
            self.add_error(None, 'Please provide either a CSV/XLSX file or paste phone numbers.')

        return data



class QuickLeadForm(forms.Form):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'placeholder': 'Full name'}))
    phone = forms.CharField(max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Phone number', 'inputmode': 'tel'}))

    def clean_phone(self):
        value = self.cleaned_data['phone'].strip()
        from apps.leads.phone_normalization import normalize_phone_number
        norm, reason = normalize_phone_number(value)
        if not norm:
            raise forms.ValidationError(reason or 'Enter a valid Indian or Bangladesh mobile number.')
        return norm

QuickLeadFormSet = forms.formset_factory(QuickLeadForm, extra=10, max_num=1000, validate_max=True, min_num=1, validate_min=True)


class AdmissionForm(forms.ModelForm):
    source_type = forms.ChoiceField(
        choices=[
            ('existing', 'Existing CRM Lead'),
            ('external', 'External Student / Direct'),
        ],
        initial='existing',
        required=False,
    )

    class Meta:
        from apps.leads.models import Admission
        model = Admission
        fields = [
            'lead',
            'caller',
            'candidate_type',
            'country',
            'walk_in_name',
            'walk_in_phone',
            'walk_in_email',
            'college',
            'course',
            'admission_date',
            'fees',
            'notes',
        ]
        widgets = {
            'admission_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Visa status, document verification, fee receipts, or counseling notes...'}),
            'walk_in_name': forms.TextInput(attrs={'placeholder': 'e.g. Rahul Sharma'}),
            'walk_in_phone': forms.TextInput(attrs={'placeholder': 'e.g. +91 98765 43210'}),
            'walk_in_email': forms.EmailInput(attrs={'placeholder': 'e.g. student@example.com'}),
            'country': forms.TextInput(attrs={'placeholder': 'e.g. India, Georgia, Uzbekistan'}),
            'college': forms.TextInput(attrs={'placeholder': 'e.g. Tbilisi State Medical University'}),
            'course': forms.TextInput(attrs={'placeholder': 'e.g. MBBS, BDS, MBA'}),
            'fees': forms.NumberInput(attrs={'placeholder': 'e.g. 350000', 'step': '0.01'}),
        }
        labels = {
            'walk_in_name': 'Student Full Name',
            'walk_in_phone': 'Phone Number',
            'walk_in_email': 'Email Address',
            'country': 'Country / Location',
            'college': 'University / College',
            'course': 'Course / Program',
            'caller': 'Attributed Caller / Counsellor',
            'fees': 'Fees / Tuition Amount (₹)',
            'admission_date': 'Admission Date',
            'candidate_type': 'Candidate Type',
            'notes': 'Admission Notes & Remarks',
        }
        error_messages = {
            'caller': {
                'required': 'Please select the responsible caller for admission attribution.',
                'null': 'Please select the responsible caller for admission attribution.',
                'invalid_choice': 'Please select a valid active caller.',
            }
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        from apps.leads.models import Lead, Admission
        from apps.accounts.models import User
        if user and getattr(user, 'role', None) == User.Role.CALLER:
            raise forms.ValidationError('Callers are not authorized to create or record admissions.')
        self.fields['caller'].queryset = User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('first_name', 'username')
        self.fields['caller'].required = True
        self.fields['lead'].queryset = Lead.objects.select_related('assigned_caller').all()
        self.fields['lead'].required = False
        self.fields['candidate_type'].required = False
        self.fields['walk_in_name'].required = False
        self.fields['walk_in_phone'].required = False

    def clean(self):
        cleaned_data = super().clean()
        from apps.leads.models import Admission
        source_type = cleaned_data.get('source_type') or 'existing'
        lead = cleaned_data.get('lead')
        caller = cleaned_data.get('caller')

        if source_type == 'existing':
            if not lead:
                self.add_error('lead', 'Please select an existing lead to record this admission.')
            cleaned_data['walk_in_name'] = ''
            cleaned_data['walk_in_phone'] = ''
            cleaned_data['walk_in_email'] = ''
            if not cleaned_data.get('candidate_type') or cleaned_data.get('candidate_type') == Admission.CandidateType.EXTERNAL:
                cleaned_data['candidate_type'] = Admission.CandidateType.LEAD
            if not cleaned_data.get('country') and lead and lead.location:
                cleaned_data['country'] = lead.location

        elif source_type == 'external':
            cleaned_data['lead'] = None

            name = cleaned_data.get('walk_in_name', '').strip()
            if not name:
                self.add_error('walk_in_name', 'Student full name is required for external student admissions.')
            else:
                cleaned_data['walk_in_name'] = name

            phone = cleaned_data.get('walk_in_phone', '').strip()
            if not phone:
                self.add_error('walk_in_phone', 'Phone number is required for external student admissions.')
            else:
                from apps.leads.phone_normalization import normalize_phone_number
                norm, reason = normalize_phone_number(phone)
                if not norm:
                    self.add_error('walk_in_phone', reason or 'Enter a valid Indian or Bangladesh mobile number.')
                else:
                    cleaned_data['walk_in_phone'] = norm

            if not cleaned_data.get('candidate_type') or cleaned_data.get('candidate_type') == Admission.CandidateType.LEAD:
                cleaned_data['candidate_type'] = Admission.CandidateType.EXTERNAL

        return cleaned_data


class ServiceForm(forms.ModelForm):
    class Meta:
        from apps.leads.models import Service
        model = Service
        fields = ['name', 'code', 'description', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def clean_code(self):
        code = self.cleaned_data.get('code', '').strip().upper()
        import re
        if not re.match(r'^[A-Z][A-Z0-9_]*$', code):
            raise forms.ValidationError('Use an uppercase code with letters, numbers and underscores (e.g. MBBS, MBA).')
        return code


class CallerServiceForm(forms.Form):
    designation = forms.CharField(max_length=100, required=False, label="Designation / Title")
    is_active = forms.BooleanField(required=False, label="Caller Eligibility (Active)")
    services = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Assigned Services",
        help_text="Check the services this caller is authorized and eligible to handle.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.leads.models import Service
        self.fields['services'].queryset = Service.objects.all().order_by('name')


class WebsiteSourceForm(forms.ModelForm):
    allowed_origins_raw = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': "https://example.com\nhttps://portal.example.com"}),
        required=False,
        label="Allowed HTTPS Origins (CORS)",
        help_text="One HTTPS origin per line. Wildcards and paths are not allowed.",
    )

    class Meta:
        from apps.leads.models import WebsiteSource
        model = WebsiteSource
        fields = ['name', 'code', 'default_service', 'allowed_services', 'is_active']
        widgets = {
            'allowed_services': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.leads.models import Service
        self.fields['default_service'].queryset = Service.objects.filter(is_active=True).order_by('name')
        self.fields['allowed_services'].queryset = Service.objects.all().order_by('name')
        if self.instance and self.instance.pk and self.instance.allowed_origins:
            self.fields['allowed_origins_raw'].initial = "\n".join(self.instance.allowed_origins)

    def clean_code(self):
        code = self.cleaned_data.get('code', '').strip().lower()
        import re
        if not re.match(r'^[a-z0-9_-]+$', code):
            raise forms.ValidationError("Website code can only contain lowercase letters, numbers, underscores, and hyphens.")
        from apps.leads.models import WebsiteSource
        qs = WebsiteSource.objects.filter(code__iexact=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"Website code '{code}' is already in use.")
        return code

    def clean_allowed_origins_raw(self):
        raw = self.cleaned_data.get('allowed_origins_raw', '')
        if not raw:
            return []
        cleaned = []
        seen = set()
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line == '*':
                raise forms.ValidationError("Wildcard '*' is not permitted as a production origin.")
            from urllib.parse import urlparse
            parsed = urlparse(line)
            if parsed.scheme != 'https':
                raise forms.ValidationError(f"Origin '{line}' must use HTTPS.")
            if not parsed.netloc:
                raise forms.ValidationError(f"Origin '{line}' must include a valid domain.")
            if parsed.path and parsed.path != '/':
                raise forms.ValidationError(f"Origin '{line}' must not contain paths.")
            if parsed.query or parsed.fragment:
                raise forms.ValidationError(f"Origin '{line}' must not contain query parameters or fragments.")
            normalized = f"https://{parsed.netloc.lower()}"
            if normalized in seen:
                raise forms.ValidationError(f"Duplicate origin '{normalized}' detected.")
            seen.add(normalized)
            cleaned.append(normalized)
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if 'allowed_origins_raw' in self.cleaned_data:
            instance.allowed_origins = self.cleaned_data['allowed_origins_raw']
        if not instance.api_key:
            from apps.leads.models import generate_api_key
            instance.api_key = generate_api_key()
        if commit:
            instance.save()
            self.save_m2m()
        return instance

