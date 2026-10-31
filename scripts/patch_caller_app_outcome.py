with open('E:/react/caller-app/src/app/call-outcome.tsx', 'r', encoding='utf-8') as f:
    text = f.read()

# Normalize
norm = text.replace('\r\n', '\n')

old_check = """    // Caller mandatory follow-up check whenever follow-up is available (not required for interested leads)
    if (isCaller && selectedOutcome !== 'interested' && allowsContact(selectedOutcome) && (!apostille || ['call_back', 'follow_up_required'].includes(selectedOutcome))) {
      if (!followUpDate || !hasSelectedDate) {
        Alert.alert('Follow-up Date Required', 'Please select a follow-up date for this lead.');
        return;
      }
      if (!hasSelectedTime) {
        Alert.alert('Follow-up Time Required', 'Please select a follow-up time for this lead.');
        return;
      }
    }
    // If follow-up date was selected for an interested lead, time is required to complete the reminder
    if (selectedOutcome === 'interested' && followUpDate && hasSelectedDate && !hasSelectedTime) {
      Alert.alert('Follow-up Time Required', 'Please select a follow-up time for this lead.');
      return;
    }"""

new_check = """    // Caller mandatory follow-up check whenever follow-up is available (not required for interested or B2B leads)
    if (isCaller && !['interested', 'b2b'].includes(selectedOutcome) && allowsContact(selectedOutcome) && (!apostille || ['call_back', 'follow_up_required'].includes(selectedOutcome))) {
      if (!followUpDate || !hasSelectedDate) {
        Alert.alert('Follow-up Date Required', 'Please select a follow-up date for this lead.');
        return;
      }
      if (!hasSelectedTime) {
        Alert.alert('Follow-up Time Required', 'Please select a follow-up time for this lead.');
        return;
      }
    }
    // If follow-up date was selected for an interested or B2B lead, time is required to complete the reminder
    if (['interested', 'b2b'].includes(selectedOutcome) && followUpDate && hasSelectedDate && !hasSelectedTime) {
      Alert.alert('Follow-up Time Required', 'Please select a follow-up time for this lead.');
      return;
    }"""

old_ui = """                📅 Follow-up Date & Time {isCaller && selectedOutcome !== 'interested' && (!apostille || ['call_back', 'follow_up_required'].includes(selectedOutcome)) && <Text style={{ color: colors.danger }}>*</Text>}
              </Text>
              {isCaller && selectedOutcome !== 'interested' && (!apostille || ['call_back', 'follow_up_required'].includes(selectedOutcome)) && (
                <View style={{ backgroundColor: colors.accentSoft, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 6 }}>
                  <Text style={{ fontSize: 11, fontWeight: '700', color: colors.primary }}>Required</Text>
                </View>
              )}
            </View>

            <Text style={styles.phone}>
              {selectedOutcome === 'interested'
                ? 'Optional — select date and time only if a reminder is needed'
                : isCaller
                ? 'Mandatory for callers — select date and time before saving'
                : ['call_back', 'follow_up_required'].includes(selectedOutcome)
                ? 'Required for Call Back'
                : 'Optional - leave empty if no reminder is needed'}
            </Text>

            {followUpDate && !['call_back', 'follow_up_required'].includes(selectedOutcome) && (!isCaller || selectedOutcome === 'interested') && ("""

new_ui = """                📅 Follow-up Date & Time {isCaller && !['interested', 'b2b'].includes(selectedOutcome) && (!apostille || ['call_back', 'follow_up_required'].includes(selectedOutcome)) && <Text style={{ color: colors.danger }}>*</Text>}
              </Text>
              {isCaller && !['interested', 'b2b'].includes(selectedOutcome) && (!apostille || ['call_back', 'follow_up_required'].includes(selectedOutcome)) && (
                <View style={{ backgroundColor: colors.accentSoft, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 6 }}>
                  <Text style={{ fontSize: 11, fontWeight: '700', color: colors.primary }}>Required</Text>
                </View>
              )}
            </View>

            <Text style={styles.phone}>
              {['interested', 'b2b'].includes(selectedOutcome)
                ? 'Optional — select date and time only if a reminder is needed'
                : isCaller
                ? 'Mandatory for callers — select date and time before saving'
                : ['call_back', 'follow_up_required'].includes(selectedOutcome)
                ? 'Required for Call Back'
                : 'Optional - leave empty if no reminder is needed'}
            </Text>

            {followUpDate && !['call_back', 'follow_up_required'].includes(selectedOutcome) && (!isCaller || ['interested', 'b2b'].includes(selectedOutcome)) && ("""

assert old_check in norm, "old_check not found"
assert old_ui in norm, "old_ui not found"

norm = norm.replace(old_check, new_check).replace(old_ui, new_ui)
with open('E:/react/caller-app/src/app/call-outcome.tsx', 'w', encoding='utf-8') as f:
    f.write(norm)

print("Successfully updated call-outcome.tsx!")

