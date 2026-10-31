with open('apps/web/performance_views.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = """        talk_time_formatted = (
            f"{int(talk_time_sec // 3600)}h {int((talk_time_sec % 3600) // 60)}m"
            if talk_time_sec >= 3600
            else f"{int(talk_time_sec // 60)}m {int(talk_time_sec % 60)}s"
        )

        result_presets[p_key] = {
            'label': cfg['label'],
            'categories': cfg['categories'],
            'ceiling': ceiling,
            'max_val': max_val,
            'total_calls': total_calls,
            'connected_calls': connected_calls,
            'talk_time_display': talk_time_formatted,
            'data': total_calls_data,
            'connected_data': connected_calls_data,
        }"""

replacement = """        talk_time_formatted = (
            f"{int(talk_time_sec // 3600)}h {int((talk_time_sec % 3600) // 60)}m"
            if talk_time_sec >= 3600
            else f"{int(talk_time_sec // 60)}m {int(talk_time_sec % 60)}s"
        )
        avg_talk_time_sec = int(talk_time_sec / connected_calls) if connected_calls > 0 else (int(talk_time_sec / total_calls) if total_calls > 0 else 0)
        avg_talk_time_formatted = (
            f"{int(avg_talk_time_sec // 60)}m {int(avg_talk_time_sec % 60)}s"
            if avg_talk_time_sec >= 60
            else f"{int(avg_talk_time_sec)}s"
        )
        conn_rate = round((connected_calls / total_calls) * 100) if total_calls > 0 else 0

        result_presets[p_key] = {
            'label': cfg['label'],
            'categories': cfg['categories'],
            'ceiling': ceiling,
            'max_val': max_val,
            'total_calls': total_calls,
            'connected_calls': connected_calls,
            'conn_rate': conn_rate,
            'talk_time_display': talk_time_formatted,
            'avg_talk_time_display': avg_talk_time_formatted,
            'data': total_calls_data,
            'connected_data': connected_calls_data,
        }"""

if target in content:
    content = content.replace(target, replacement)
    with open('apps/web/performance_views.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Successfully patched performance_views.py")
else:
    print("Target not found!")

