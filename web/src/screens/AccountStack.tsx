import { Check } from "lucide-react";
import { useReducedMotion } from "motion/react";
import { type CSSProperties, type PointerEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { components } from "@/api/schema";
import { Money } from "@/components/Money";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ASPECT, BACK_SCRIM, FACE_HUE, FRONT_SCRIM, RADIUS } from "@/lib/cardArt";
import { type Hover, type Pose, stackLayout, stackPoses } from "@/lib/cardPoses";
import type { SceneFace } from "@/three/cardScene";
import { useCardArt } from "./useCardArt";
import { useCardScene } from "./useCardScene";
import { useCardSprings } from "./useCardSprings";

export { SELECT_SPRING } from "./useCardSprings";

type Account = components["schemas"]["AccountOut"];
type Balances = components["schemas"]["BalancesOut"];

// Generic hues for banks without a known colour, all outside the violet band 235-290.
const HUES = [172, 38, 345, 140, 15, 95];
/** The hue an Account wears on its card, so the per-account breakdown matches the stack. */
export const accountHue = (a: Account, i: number): number =>
  (a.art ? FACE_HUE[a.art] : undefined) ?? HUES[i % HUES.length] ?? 200;
const PERSPECTIVE = 1400; // px; the WebGL camera sits at the same distance
// The canvas overflows the stack so lifted cards and tilts aren't clipped; sideways only as
// far as the phone gutter, so it never makes the page scroll.
const MX = 16;
const MY = 48;
const RISE_MS = 160; // a chosen card rises towards you, then settles at the front
// Over a bank's own art the labels sit in one darkened band: the bottom of what shows of a
// card behind, or the bottom of the front card's face. Never over the art's top edge.
const BAND = 58; // px
const FRONT_BAND = 104;
const SETTLE_MS = 420;
// Drag or side-scroll the stack to turn the front card over (#93); this long after the input
// stops, the stack settles on the nearest card: the next one past half a turn.
const SNAP_MS = 170;
const TURNED_DEG = 12; // a hover tilts a card up to 10deg; beyond this it is being turned
const DRAG_PX = 8; // movement before a press becomes a drag, so a tap stays a click

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });

type Face = {
  key: string;
  id: number | undefined;
  title: string;
  masked?: string;
  balance: { minor: number; currency: string } | null;
  matches: boolean | null; // null: the bank gives no balance, so nothing to match
  rateNeeded?: string[]; // currencies whose balance has no rate, so the total is unknown
  hue: number | null;
  art: string | null;
};

function faces(accounts: Account[], balances?: Balances): Face[] {
  const currency = accounts[0]?.currency ?? "GBP";
  // one currency sums as is; mixed ones take the API's GBP total (#156), never a guess
  const same = accounts.every((a) => a.currency === currency);
  // a bank with no balance (HSBC) leaves the total unknown, not smaller
  const known = accounts.every((a) => a.bank_balance !== null);
  const needed = (balances?.accounts ?? []).filter((x) => x.gbp === null).map((x) => x.currency);
  const status = (a: Account[]) =>
    a.some((x) => x.gaps.length > 0) ? false : a.every((x) => x.bank_balance !== null) || null;
  const all: Face = {
    key: "all",
    id: undefined,
    title: "All accounts",
    balance:
      same && known
        ? { minor: accounts.reduce((n, a) => n + (a.bank_balance?.minor ?? 0), 0), currency }
        : same
          ? null
          : (balances?.total ?? null),
    matches: status(accounts),
    rateNeeded: same || !known ? [] : [...new Set(needed)],
    hue: null,
    art: null,
  };
  return [
    all,
    ...accounts.map((a, i) => ({
      key: String(a.id),
      id: a.id,
      title: a.bank,
      masked: a.masked_number,
      balance: a.bank_balance,
      matches: status([a]),
      hue: accountHue(a, i),
      art: a.art,
    })),
  ];
}

/** The page colour behind the stack, which each card's back fades towards (#93). */
const pageColour = () =>
  getComputedStyle(document.documentElement).getPropertyValue("--background").trim() || "#f3f5f7";

const webgl = () => typeof WebGLRenderingContext !== "undefined";
const transform = (p: Pose) =>
  `translate3d(${p.x}px, ${p.y}px, ${p.z}px) rotateX(${p.rx}deg) rotateY(${p.ry}deg) rotateZ(${p.rz}deg) scale(${p.s})`;

export function AccountStack({
  accounts,
  balances,
  selected,
  onSelect,
  onPreview,
  onHot,
}: {
  accounts: Account[];
  balances?: Balances; // GBP balances from /api/fx/balances, for a mixed-currency total
  selected?: number;
  onSelect: (id: number | undefined) => void;
  onPreview?: (id: number | undefined) => void; // a pointer is on a card: fetch its view early
  onHot?: (id: number | undefined) => void; // a pointer or keyboard is on a card: light its share elsewhere
}) {
  const reduce = useReducedMotion() ?? false;
  const all = useMemo(() => faces(accounts, balances), [accounts, balances]);
  const [hover, setHover] = useState<Hover | null>(null);
  const [rising, setRising] = useState<number | null>(null);
  const [width, setWidth] = useState(0);
  const art = useCardArt(all);
  const stack = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const [turn, setTurnState] = useState(0); // -1..1: how far the front card is turned over
  const turnNow = useRef(0); // the same, for the snap timer
  const setTurn = (v: number) => {
    turnNow.current = Math.max(-1, Math.min(1, v));
    setTurnState(turnNow.current);
  };

  const frontIndex = Math.max(0, all.findIndex((f) => f.id === selected));
  const current = all[frontIndex] as Face;
  const fan = selected === undefined;
  const cardH = width ? width / ASPECT : 200;
  const layout = stackLayout(all.length, cardH);
  const poses = stackPoses(all.length, frontIndex, {
    fan,
    reduce,
    hover,
    rising,
    flip: turn,
    peek: layout.peek,
    fanPeek: layout.fanPeek,
  });
  const heightOf = (f: Face) => (f.id === undefined ? layout.allH : cardH);
  const shows = fan ? layout.fanPeek : layout.peek; // how much of a card behind is in view
  const isFront = (i: number) => poses[i]?.layer === all.length - 1;
  // Tucked behind a chosen account, an art card shows too little for a label band: it shows
  // only its art (the labels stay for screen readers).
  const tucked = (f: Face, i: number) => !fan && !isFront(i) && Boolean(f.art && art[f.art]);
  // Where each card's label band starts (px from its top edge); null: no band (generic face).
  const band = (f: Face, i: number) => {
    if (!f.art || !art[f.art] || tucked(f, i)) return null;
    return isFront(i) ? heightOf(f) - FRONT_BAND : shows - BAND;
  };
  const bands = all.map(band);

  // One set of springs per card; the DOM and the WebGL scene both read them. `draw` runs after
  // render (a frame, or the layout effect), so `gpu` below is set by then.
  const redraw = useCardSprings(
    all,
    poses,
    { frontIndex, rising, reduce, hover, width, heightAt: (i) => heightOf(all[i] as Face) },
    (now, light, withScene) => {
      now.forEach((p, i) => {
        const el = buttons.current[i];
        if (!el) return;
        el.style.transform = transform(p);
        el.dataset.back = String(Math.abs(p.ry) > 90); // turned over: the labels would read mirrored
        // Past a hover's tilt the card is turning: over WebGL its flat labels would part from it.
        el.dataset.turned = String(Math.abs(p.ry) > TURNED_DEG);
      });
      const s = gpu.scene.current;
      if (withScene && s && gpu.onScreen.current && !document.hidden) {
        s.update(
          now,
          light,
          bands.map((top, i) => (top === null ? null : { top, front: isFront(i) })),
        );
        s.render();
      }
    },
  );

  // While the cards reshuffle, the ones going back hide their text: over the WebGL faces
  // a DOM card is see-through, so its label would float across the card in front.
  const [settling, setSettling] = useState(false);
  useEffect(() => {
    if (rising === null) return;
    const t = setTimeout(() => setRising(null), RISE_MS);
    return () => clearTimeout(t);
  }, [rising]);
  const shown = useRef(selected);
  useEffect(() => {
    if (shown.current === selected) return;
    shown.current = selected;
    setSettling(true);
    const t = setTimeout(() => setSettling(false), SETTLE_MS);
    return () => clearTimeout(t);
  }, [selected]);

  // Width of the stack: the cards keep the ID-1 aspect ratio.
  useLayoutEffect(() => {
    const el = stack.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e?.contentRect.width ?? 0)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // WebGL: one lazy-loaded canvas for the whole stack, never under reduced motion.
  const wantGl = !reduce && webgl() && width > 0;
  const allH = layout.allH;
  const sceneFaces = useMemo<SceneFace[]>(
    () =>
      all.map((f) => ({ art: (f.art && art[f.art]) || null, hue: f.hue, h: f.id === undefined ? allH : cardH })),
    [all, art, allH, cardH],
  );
  const stackH = layout.height;
  const [page, setPage] = useState(pageColour);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const change = () => setPage(pageColour());
    mq.addEventListener?.("change", change);
    return () => mq.removeEventListener?.("change", change);
  }, []);
  const gpu = useCardScene(
    canvas,
    stack,
    wantGl,
    { perspective: PERSPECTIVE, width, height: stackH, mx: MX, my: MY, faces: sceneFaces, page },
    redraw,
  );
  const gl = gpu.gl;

  // Snap: settle on the nearest card once the drag or side-scroll has stopped for SNAP_MS.
  const snapTimer = useRef(0);
  const snapNow = useRef(() => {});
  snapNow.current = () => {
    const step = Math.round(turnNow.current);
    setTurn(0);
    if (!step) return;
    const i = (frontIndex + step + all.length) % all.length;
    setRising(i);
    onSelect(all[i]?.id);
  };
  const armSnap = () => {
    clearTimeout(snapTimer.current);
    snapTimer.current = window.setTimeout(() => snapNow.current(), SNAP_MS);
  };
  useEffect(() => () => clearTimeout(snapTimer.current), []);
  const press = useRef<{ id: number; x: number; y: number; from: number; dragging: boolean; card: number } | null>(null);
  // A pointer press ends in a choice at pointerup, of the card it went down on. The click
  // after it is swallowed: while the stack reshuffles the cards move under a still pointer,
  // so down and up can land on different cards and the browser sends that click to neither.
  const swallow = useRef(false);
  const choose = (i: number) => {
    if (i !== frontIndex) setRising(i);
    onSelect(all[i]?.id);
  };
  const widthNow = useRef(width);
  widthNow.current = width || 1;
  // Sideways wheel (a trackpad swipe) turns the card; vertical stays the page's scroll. Not a
  // React prop: React's wheel listener is passive, so it could not keep the page from moving.
  useEffect(() => {
    const el = stack.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
      e.preventDefault();
      setTurn(turnNow.current + e.deltaX / widthNow.current);
      armSnap();
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, [accounts.length === 0]); // the stack mounts with the first account; the handler reads refs
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    swallow.current = false;
    if (e.button !== 0 || press.current) return; // one pointer at a time
    const hit = (e.target as Element).closest?.(".account-card");
    const card = buttons.current.findIndex((b) => b !== null && b === hit);
    press.current = { id: e.pointerId, x: e.clientX, y: e.clientY, from: turnNow.current, dragging: false, card };
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const p = press.current;
    if (!p || p.id !== e.pointerId) return;
    const dx = e.clientX - p.x;
    if (!p.dragging) {
      if (Math.abs(e.clientY - p.y) > DRAG_PX && Math.abs(e.clientY - p.y) > Math.abs(dx)) {
        press.current = null; // a vertical move is the page scrolling
        return;
      }
      if (Math.abs(dx) <= DRAG_PX) return;
      p.dragging = true;
      clearTimeout(snapTimer.current);
      e.currentTarget.setPointerCapture?.(e.pointerId);
    }
    setTurn(p.from - dx / widthNow.current); // drag left: towards the next card
  };
  const onPointerEnd = (e: PointerEvent<HTMLDivElement>) => {
    const p = press.current;
    if (!p || p.id !== e.pointerId) return;
    press.current = null;
    if (p.dragging || turnNow.current !== 0) armSnap();
    if (e.type !== "pointerup") return;
    // the click that follows (a drag's included) is not a second choice; cleared after it,
    // so a later Enter or Space is never swallowed
    swallow.current = true;
    setTimeout(() => (swallow.current = false), 0);
    if (!p.dragging && turnNow.current === 0 && p.card >= 0) choose(p.card);
  };

  // CSS cards show the same art as a background image.
  const cssArt = useMemo(() => {
    if (gl) return {};
    return Object.fromEntries(Object.entries(art).map(([k, c]) => [k, `url(${c.toDataURL()})`]));
  }, [art, gl]);

  if (accounts.length === 0) {
    return (
      <Card>
        <h2 className="text-heading">Accounts</h2>
        <p className="mt-2 text-label text-muted-foreground">
          No accounts yet. Run <code>fa import</code> with a statement export.
        </p>
      </Card>
    );
  }

  const inView = selected === undefined ? accounts : accounts.filter((a) => a.id === selected);
  const gaps = inView.flatMap((a) => a.gaps.map((g) => ({ a, g })));
  const fine = () => typeof matchMedia === "function" && matchMedia("(hover: hover) and (pointer: fine)").matches;
  const point = (i: number) => (e: PointerEvent<HTMLButtonElement>) => {
    if (e.pointerType !== "mouse" || !fine()) return;
    const r = e.currentTarget.getBoundingClientRect();
    setHover({ index: i, px: (e.clientX - r.left) / r.width, py: (e.clientY - r.top) / r.height });
  };

  return (
    <section aria-labelledby="accounts-title" className="flex flex-col gap-4">
      <CardHeader className="mb-0">
        <CardTitle id="accounts-title">Accounts</CardTitle>
        <CardDescription>Choose one to filter the Overview</CardDescription>
      </CardHeader>
      <div
        ref={stack}
        role="group"
        aria-label="Accounts"
        className="card-stack"
        data-gl={gl}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        style={{ height: stackH, perspective: PERSPECTIVE }}
      >
        {wantGl ? (
          <canvas
            ref={canvas}
            aria-hidden="true"
            className="card-canvas"
            style={{ left: -MX, top: -MY, width: width + 2 * MX, height: stackH + 2 * MY }}
          />
        ) : null}
        {all.map((f, i) => {
          const pose = poses[i] as Pose;
          const on = f === current;
          const image = f.art ? cssArt[f.art] : undefined;
          const b = bands[i];
          return (
            <button
              key={f.key}
              ref={(el) => {
                buttons.current[i] = el;
              }}
              type="button"
              aria-pressed={on}
              onClick={(e) => {
                // a pointer press already chose at pointerup; detail 0 is Enter, Space or a
                // screen reader, which always chooses
                if (swallow.current && e.detail !== 0) return;
                choose(i);
              }}
              onPointerEnter={() => {
                onPreview?.(f.id);
                onHot?.(f.id);
              }}
              onPointerMove={point(i)}
              onPointerLeave={() => {
                setHover((h) => (h?.index === i ? null : h));
                onHot?.(undefined);
              }}
              onFocus={(e) => {
                if (e.currentTarget.matches(":focus-visible")) {
                  setHover({ index: i, px: 0.5, py: 0.5 });
                  onHot?.(f.id);
                }
              }}
              onBlur={() => {
                setHover((h) => (h?.index === i ? null : h));
                onHot?.(undefined);
              }}
              className="account-card text-left"
              data-front={pose.layer === all.length - 1}
              data-selected={on}
              data-all={f.id === undefined}
              data-art={Boolean(f.art && art[f.art])}
              data-hover={hover?.index === i}
              data-settling={settling && !on}
              data-tucked={tucked(f, i)}
              style={
                {
                  zIndex: pose.layer + 1,
                  height: heightOf(f),
                  borderRadius: width ? width * RADIUS : undefined,
                  transform: transform(pose),
                  ...(f.hue === null ? {} : { "--h": f.hue }),
                  ...(b === null || b === undefined ? {} : { "--band": `${b}px` }),
                  ...(image
                    ? {
                        backgroundImage:
                          b === null || b === undefined
                            ? image
                            : `linear-gradient(to bottom, transparent ${b}px, rgb(6 8 10 / ${isFront(i) ? FRONT_SCRIM : BACK_SCRIM}) ${b + (isFront(i) ? 44 : 28)}px), ${image}`,
                      }
                    : {}),
                } as CSSProperties
              }
            >
              {/* Slots are placed by grid areas in index.css (.account-card): a back card shows
                  title and balance in its peek; the front card sets its balance as a figure. */}
              <span data-slot="title" className="min-w-0">
                <span className="block truncate text-row font-semibold">{f.title}</span>
                {f.masked ? <span className="block text-label opacity-80">{f.masked}</span> : null}
              </span>
              {f.balance ? (
                <Money value={f.balance} odometer={f.key} className="account-balance text-row font-semibold" />
              ) : null}
              <span data-slot="status" className="flex items-center gap-1.5 text-label font-semibold">
                {f.rateNeeded?.length ? (
                  `Total unknown: rate needed for ${f.rateNeeded.join(", ")}`
                ) : f.matches === null ? (
                  f.id === undefined ? "Total unknown: a bank gives no balance" : "No balance from bank"
                ) : f.matches ? (
                  <>
                    <Check className="size-3.5" aria-hidden="true" />
                    Matches ledger
                  </>
                ) : (
                  <span className="rounded-full bg-warning px-3 py-1 text-warning-foreground">
                    Find it
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </div>
      <p role="status" className="sr-only">
        Showing {current.masked ? `${current.title} ${current.masked}` : current.title}
      </p>
      {gaps.length === 0 ? null : (
        <ul className="spotlight flex flex-col gap-2 text-label text-muted-foreground">
          {gaps.map(({ a, g }) => (
            <li key={`${a.id}-${g.from_date}-${g.to_date}`}>
              {a.bank} {a.masked_number}: between {day(g.from_date)} and {day(g.to_date)} your
              bank shows <Money value={g.amount} className="font-semibold text-foreground" />{" "}
              {g.amount.minor > 0 ? "more" : "less"} than your ledger. Import the statement
              covering those dates.
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
