with open('E:/react/caller-app/scripts/test-call-outcome-flow.cjs', 'r', encoding='utf-8') as f:
    text = f.read()

norm = text.replace('\r\n', '\n')

old_part = """test('non-Interested caller outcomes never offer Forward to Counselor', async () => {"""

new_part = """test('caller B2B saves without course/year and without follow-up, then offers optional Forward to Counselor', async () => {
  const app = await screen({ role: 'CALLER' });
  button(app.render(), 'B2B').props.onPress();
  await button(app.render(), 'Save Outcome').props.onPress();
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].body.outcome, 'B2B');
  assert.equal('selected_course' in app.calls[0].body, false);
  assert.equal('expected_admission_year' in app.calls[0].body, false);
  assert.equal('callback_at' in app.calls[0].body, false);
  let forward = nodes(app.render()).find(n => n.type === 'ForwardCounselorModal');
  assert.equal(forward.props.visible, false);
  const [title, , buttons] = app.alerts.at(-1);
  assert.equal(title, 'Outcome saved');
  buttons.find(b => b.text === 'Continue').onPress();
  forward = nodes(app.render()).find(n => n.type === 'ForwardCounselorModal');
  assert.equal(forward.props.visible, true);
  assert.equal(forward.props.leadId, 7);
  forward.props.onClose();
  assert.equal(app.navigation.length, 1);
});

test('non-Interested and non-B2B caller outcomes never offer Forward to Counselor', async () => {"""

assert old_part in norm, "old_part not found"
norm = norm.replace(old_part, new_part)

with open('E:/react/caller-app/scripts/test-call-outcome-flow.cjs', 'w', encoding='utf-8') as f:
    f.write(norm)

print("Successfully updated test-call-outcome-flow.cjs!")

