with open('apps/web/static/web/crm.css', 'r', encoding='utf-8') as f:
    content = f.read()

target = """.sidebar-search-box {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.035);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 9px;
  padding: 7px 10px;
  margin: 12px 0 10px;
  transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  flex-shrink: 0;
}"""

replacement = """.sidebar-search-box {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  min-height: 38px;
  max-height: 38px;
  box-sizing: border-box;
  background: rgba(255, 255, 255, 0.035);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 9px;
  padding: 0 10px;
  margin: 12px 0 10px;
  transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  flex-shrink: 0;
}"""

input_target = """.sidebar-search-box input {
  flex: 1;
  min-width: 0;
  border: none;
  background: none;
  outline: none;
  font-size: 12px;
  color: #f1f5f9;
  padding: 0;
}"""

input_replacement = """.sidebar-search-box input {
  flex: 1;
  min-width: 0;
  height: 100%;
  box-sizing: border-box;
  border: none;
  background: none;
  outline: none;
  font-size: 12px;
  color: #f1f5f9;
  padding: 0;
}"""

if target in content and input_target in content:
    content = content.replace(target, replacement).replace(input_target, input_replacement)
    with open('apps/web/static/web/crm.css', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Successfully patched crm.css")
else:
    print("Target not found in crm.css!")

