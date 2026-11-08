// synthetic: made-up figures and Merchants for the public landing page (#300). No real bank or shop.
import type { CategoryGroup } from "@/api/client";
import type { Category } from "@/components/CategoryChip";
import type { Money } from "@/lib/money";

const gbp = (minor: number): Money => ({ minor, currency: "GBP" });

/** Top-level Categories in taxonomy order, so the chips take the app's own hues. */
export const GROUPS: CategoryGroup[] = [
  { name: "Groceries", subs: ["Groceries"] },
  { name: "Eating out", subs: ["Cafes", "Restaurants"] },
  { name: "Transport", subs: ["Trains", "Fuel"] },
  { name: "Bills", subs: ["Energy", "Phone"] },
];

export type Row = { merchant: string; amount: Money; category: Category };

export const OVERVIEW = {
  month: "October",
  spent: gbp(184_236),
  lastMonth: gbp(196_010),
  top: [
    { name: "Groceries", amount: gbp(48_210), share: 0.62 },
    { name: "Bills", amount: gbp(41_500), share: 0.54 },
    { name: "Eating out", amount: gbp(22_640), share: 0.29 },
    { name: "Transport", amount: gbp(15_880), share: 0.2 },
  ],
};

export const REVIEW: Row[] = [
  { merchant: "Corner Grocer", amount: gbp(-4_210), category: ["Groceries", "Groceries"] },
  { merchant: "Harbour Rail", amount: gbp(-2_350), category: ["Transport", "Trains"] },
  { merchant: "Little Kettle Cafe", amount: gbp(-640), category: ["To review", "To review"] },
];

/** The answer's figures come from one tool result, never from the model (ADR 0007). */
export const ANSWER = {
  question: "How much did I spend on eating out in October?",
  tool: "spending_by_category",
  total: gbp(22_640),
  count: 14,
  previous: gbp(18_920),
};

/** The gateway chapter's one line and its placeholders. */
export const GATEWAY = { amount: gbp(4_210), merchant: "Corner Grocer", amountToken: "{P1}", merchantToken: "{M1}" };

export const PRINCIPLES = [
  "Your ledger lives in one file on your Mac.",
  "Every figure comes from a calculation, never a guess.",
  "Unsure categories wait for you, not for the model.",
  "The cloud is off until you turn it on, and sees placeholders.",
  "It reads your accounts. It never moves money.",
];

/** Every Merchant in this file, for the no-real-names test. */
export const MERCHANTS = [GATEWAY.merchant, ...REVIEW.map((r) => r.merchant)];
