from django.contrib import messages
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.leads.models import Service, WebsiteLeadSubmission, WebsiteSource
from .forms import WebsiteSourceForm
from .views import page, workspace


@workspace(management=True)
def website_integrations_list(request):
    base_sources = (
        WebsiteSource.objects.select_related("default_service")
        .prefetch_related("allowed_services")
        .annotate(
            submission_count=Count("submissions", distinct=True),
            last_submitted_at=Max("submissions__submitted_at"),
        )
        .order_by("-is_active", "name", "pk")
    )

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    service_filter = request.GET.get("service", "").strip()
    tab = request.GET.get("tab", "all").strip()

    # Prioritize tab if status isn't explicitly set
    if not status_filter and tab in ("active", "inactive"):
        status_filter = tab

    scoped_sources = base_sources
    if q:
        scoped_sources = scoped_sources.filter(
            Q(name__icontains=q)
            | Q(code__icontains=q)
        )

    if service_filter:
        scoped_sources = scoped_sources.filter(
            Q(default_service__code=service_filter)
            | Q(allowed_services__code=service_filter)
        ).distinct()

    # Dynamic metrics calculated on the scoped/filtered queryset
    total_sources = scoped_sources.count()
    active_sources = scoped_sources.filter(is_active=True).count()
    inactive_sources = scoped_sources.filter(is_active=False).count()
    total_submissions = scoped_sources.aggregate(total=Sum("submission_count"))["total"] or 0

    # Display list filtered by status/tab
    display_sources = scoped_sources
    if status_filter == "active":
        display_sources = scoped_sources.filter(is_active=True)
    elif status_filter == "inactive":
        display_sources = scoped_sources.filter(is_active=False)

    active_filters_count = sum([
        bool(q),
        bool(service_filter),
        bool(status_filter and status_filter != "all"),
    ])

    all_services = Service.objects.all().order_by("name")

    return page(
        request,
        "website_integrations",
        "website-integrations",
        sources=display_sources,
        total_sources=total_sources,
        active_sources=active_sources,
        inactive_sources=inactive_sources,
        total_submissions=total_submissions,
        all_services=all_services,
        search_query=q,
        selected_status=status_filter,
        selected_service=service_filter,
        selected_tab=tab,
        active_filters_count=active_filters_count,
    )


@workspace(management=True)
def website_integration_create(request):
    form = WebsiteSourceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        source = form.save()
        # Stash new raw API key in session for one-time display
        request.session["flash_website_key"] = {
            "id": source.pk,
            "key": source.api_key,
        }
        messages.success(
            request,
            f'Website integration "{source.name}" created successfully.',
        )
        return redirect("web:website-integration-credential", pk=source.pk)

    return page(
        request,
        "website_integration_form",
        "website-integrations",
        form=form,
        mode="create",
        title="Add Website Integration",
    )


@workspace(management=True)
def website_integration_credential(request, pk):
    source = get_object_or_404(
        WebsiteSource.objects.select_related("default_service").prefetch_related("allowed_services"),
        pk=pk,
    )
    flash_data = request.session.pop("flash_website_key", None)
    key_available = False
    api_key = None

    if flash_data and flash_data.get("id") == source.pk:
        key_available = True
        api_key = flash_data.get("key")

    host = request.get_host()
    scheme = "https" if request.is_secure() else "http"
    base_url = f"{scheme}://{host}"

    return page(
        request,
        "website_credential_show",
        "website-integrations",
        source=source,
        key_available=key_available,
        api_key=api_key,
        base_url=base_url,
    )


@workspace(management=True)
def website_integration_detail(request, pk):
    source = get_object_or_404(
        WebsiteSource.objects.select_related("default_service").prefetch_related("allowed_services"),
        pk=pk,
    )
    recent_submissions = (
        source.submissions.select_related("lead", "service_type")
        .order_by("-submitted_at")[:25]
    )
    total_submissions = source.submissions.count()
    duplicate_submissions = source.submissions.filter(is_duplicate=True).count()
    new_submissions = total_submissions - duplicate_submissions

    host = request.get_host()
    scheme = "https" if request.is_secure() else "http"
    base_url = f"{scheme}://{host}"

    return page(
        request,
        "website_integration_detail",
        "website-integrations",
        source=source,
        recent_submissions=recent_submissions,
        total_submissions=total_submissions,
        duplicate_submissions=duplicate_submissions,
        new_submissions=new_submissions,
        base_url=base_url,
    )


@workspace(management=True)
def website_integration_edit(request, pk):
    source = get_object_or_404(WebsiteSource, pk=pk)
    form = WebsiteSourceForm(request.POST or None, instance=source)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(
            request,
            f'Website integration "{source.name}" updated successfully.',
        )
        return redirect("web:website-integration-detail", pk=source.pk)

    return page(
        request,
        "website_integration_form",
        "website-integrations",
        form=form,
        source=source,
        mode="edit",
        title=f"Edit {source.name}",
    )


@workspace(management=True)
@require_POST
def website_integration_toggle_active(request, pk):
    source = get_object_or_404(WebsiteSource, pk=pk)
    source.is_active = not source.is_active
    source.save(update_fields=["is_active", "updated_at"])

    status_label = "activated" if source.is_active else "deactivated"
    messages.success(
        request,
        f'Website integration "{source.name}" has been {status_label}.',
    )
    next_url = request.POST.get("next")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("web:website-integrations")


@workspace(management=True)
@require_POST
def website_integration_regenerate_key(request, pk):
    source = get_object_or_404(WebsiteSource, pk=pk)
    new_key = source.regenerate_api_key()

    # Flash in session for one-time display
    request.session["flash_website_key"] = {
        "id": source.pk,
        "key": new_key,
    }
    messages.warning(
        request,
        f'API key for "{source.name}" has been regenerated. The previous key is now invalidated.',
    )
    return redirect("web:website-integration-credential", pk=source.pk)
