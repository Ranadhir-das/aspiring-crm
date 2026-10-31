import os, sys

file_path = r'E:\react\aspiring-crm\apps\web\views.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

old_code = """    query = between(ActivityLog.objects.filter(actor=caller).select_related('lead'), 'created_at', window).order_by('-created_at')
    ledger_page = Paginator(context.pop('ledger'), 25).get_page(request.GET.get('ledger_page'))
    return page(request, 'caller_detail', 'performance', caller=caller, records=paginate(request, query),
                filters=filters, window=window, report_valid=valid, ledger=ledger_page,
                adjustments=adjustments, adjustment_form=adjustment_form, milestone_form=milestone_form,
                can_adjust=can_manage(request.user) and caller.role == User.Role.CALLER, **context)"""

new_code = """    from .performance_views import build_individual_caller_chart_data
    caller_chart_data = build_individual_caller_chart_data(caller)
    query = between(ActivityLog.objects.filter(actor=caller).select_related('lead'), 'created_at', window).order_by('-created_at')
    ledger_page = Paginator(context.pop('ledger'), 25).get_page(request.GET.get('ledger_page'))
    return page(request, 'caller_detail', 'performance', caller=caller, records=paginate(request, query),
                filters=filters, window=window, report_valid=valid, ledger=ledger_page,
                adjustments=adjustments, adjustment_form=adjustment_form, milestone_form=milestone_form,
                can_adjust=can_manage(request.user) and caller.role == User.Role.CALLER,
                caller_chart_data=caller_chart_data, **context)"""

if old_code in content:
    content = content.replace(old_code, new_code)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: caller_detail updated in views.py")
else:
    print("WARNING: old_code not found in views.py")

