with open('apps/web/templates/web/performance.html', 'r', encoding='utf-8') as f:
    perf_html = f.read()

perf_html = perf_html.replace('performance-command.css?v=2', 'performance-command.css?v=3')
perf_html = perf_html.replace('performance-chart.js?v=2', 'performance-chart.js?v=3')

with open('apps/web/templates/web/performance.html', 'w', encoding='utf-8') as f:
    f.write(perf_html)

with open('apps/web/templates/web/caller_detail.html', 'r', encoding='utf-8') as f:
    caller_html = f.read()

caller_html = caller_html.replace('performance-command.css?v=3', 'performance-command.css?v=4')
caller_html = caller_html.replace('caller-detail.css?v=3', 'caller-detail.css?v=4')
caller_html = caller_html.replace('caller-detail-chart.js?v=3', 'caller-detail-chart.js?v=4')

with open('apps/web/templates/web/caller_detail.html', 'w', encoding='utf-8') as f:
    f.write(caller_html)

print("Successfully bumped asset versions!")

