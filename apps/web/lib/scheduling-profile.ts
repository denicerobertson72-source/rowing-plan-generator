export type RecurringActivity = Record<string, unknown>;

const isObject = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

/**
 * Applies the modern recurring-schedule choice to compatibility fields after a
 * successful scheduling edit.  It is deliberately not called while loading a
 * profile: viewing a legacy profile must never mutate it.
 */
export function normalizeModernSchedulingProfile(profile: Record<string, any>, activities: RecurringActivity[]) {
  const availability = Array.isArray(profile.weekly_availability) ? profile.weekly_availability : [];
  const availableWeekdays = availability
    .filter((entry) => isObject(entry) && entry.available !== false)
    .map((entry) => String(entry.weekday))
    .filter(Boolean);
  const normalizedActivities = activities.map((source) => {
    const activity = { ...source } as Record<string, any>;
    if (activity.activity_type !== "rest") return activity;

    if (activity.scheduling_status !== "fixed") {
      activity.fixed_days = [];
      activity.planner_may_choose_day = activity.scheduling_status === "flexible";
      // A flexible rest card means the planner chooses one permitted day. A
      // former fixed card has no allowed_days, so preserve the intended
      // flexibility by making the athlete's available week explicit.
      if (activity.scheduling_status === "flexible" && !strings(activity.allowed_days).length) {
        activity.allowed_days = availableWeekdays;
      }
    } else {
      activity.fixed_days = strings(activity.fixed_days);
      activity.planner_may_choose_day = false;
    }
    return activity;
  });

  const fixedRestDays = normalizedActivities.flatMap((activity) =>
    activity.activity_type === "rest" && activity.scheduling_status === "fixed" ? strings(activity.fixed_days) : [],
  );
  const preferences = isObject(profile.preferences) ? profile.preferences : {};
  return {
    ...profile,
    recurring_activities: normalizedActivities,
    // Modern recurring activities are authoritative to the scheduler.  Keep
    // compatibility fields coherent so old clients cannot reintroduce a fixed
    // Saturday after a modern rest edit.
    preferences: { ...preferences, fixed_rest_weekdays: fixedRestDays },
    weekly_availability: availability.map((entry) =>
      isObject(entry) ? { ...entry, fixed_rest: fixedRestDays.includes(String(entry.weekday)) } : entry,
    ),
  };
}
