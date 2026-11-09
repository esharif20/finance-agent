// synthetic: made-up shares for the hero's hover peek. No real bank, shop or person.
import { REVIEW } from "./story/story.fixture";

/** Where each made-up account's money went this month, as shares of that account's money out.
    Shares only, never amounts: the amounts on the hero all come from accounts.fixture. */
export const SPLITS: Record<string, { name: string; share: number }[]> = {
  Everyday: [
    { name: "Groceries", share: 0.31 },
    { name: "Bills", share: 0.27 },
    { name: "Eating out", share: 0.15 },
    { name: "Transport", share: 0.1 },
  ],
  "Credit card": [
    { name: "Eating out", share: 0.58 },
    { name: "Transport", share: 0.42 },
  ],
  Savings: [],
};

/** The ledger chips that drift round the hand: the landing page's own made-up rows. */
export const CHIPS = REVIEW;
