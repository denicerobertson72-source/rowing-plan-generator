/** Calendar dates in plans are athlete-facing dates, never UTC instants. */
export function localCalendarDate(now: Date = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
}
