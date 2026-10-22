/* Words for numbers, money and dates, in British English. The pound sign comes from Intl.NumberFormat,
   so no non-ASCII character is written in the source. */

const MONEY = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 0 });
const DAY = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long" });
const SHORT_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" });
const CLOCK = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
const MS_PER_DAY = 86400000;

/** "22,000" as money: 22000 -> the pound sign then "22,000"; null and blanks give "". */
export function money(n) {
  if (n === null || n === undefined || n === "" || Number.isNaN(Number(n))) return "";
  return MONEY.format(Number(n));
}

/** A pay range in words: "22,000 to 26,000" with pound signs; one end alone when the other is missing. */
export function payRange(lo, hi) {
  if (lo && hi && Number(lo) !== Number(hi)) return money(lo) + " to " + money(hi);
  return money(lo || hi);
}

function toDate(d) { return d instanceof Date ? d : new Date(d); }

/** "Thursday 22 October". */
export function dayWords(d) { return DAY.format(toDate(d)); }

/** "22 October". */
export function shortDay(d) { return SHORT_DAY.format(toDate(d)); }

/** "09:00", in this computer's time zone. */
export function clock(d) { return CLOCK.format(toDate(d)); }

/** "1 job", "3 jobs"; `many` defaults to `one` plus "s". */
export function plural(n, one, many = one + "s") { return n + " " + (n === 1 ? one : many); }

/** Whole days from today to `d`, counted on the calendar (tomorrow is 1 even at 23:59). */
export function daysFrom(d, now = new Date()) {
  const a = toDate(d), b = now;
  const ua = Date.UTC(a.getFullYear(), a.getMonth(), a.getDate());
  const ub = Date.UTC(b.getFullYear(), b.getMonth(), b.getDate());
  return Math.round((ua - ub) / MS_PER_DAY);
}

/** "today", "tomorrow", "in 5 days", "yesterday", "3 days ago". */
export function inDays(d, now = new Date()) {
  const n = daysFrom(d, now);
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n === -1) return "yesterday";
  return n > 0 ? "in " + plural(n, "day") : plural(-n, "day") + " ago";
}
