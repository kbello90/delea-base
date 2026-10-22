const dateFormat = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "UTC" });
const dateTimeFormat = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });

/** "Oct 22, 2026": the UTC calendar date, the same day the list's date filters use. */
export const formatDate = (iso: string) => dateFormat.format(new Date(iso));

/** "Oct 22, 2026, 3:04 PM UTC". */
export const formatDateTime = (iso: string) => `${dateTimeFormat.format(new Date(iso))} UTC`;

/** A due date (a plain date, no time zone) as "Nov 21, 2026". */
export const formatDay = (day: string) => dateFormat.format(new Date(`${day}T00:00:00Z`));
