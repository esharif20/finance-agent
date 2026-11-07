import { Check } from "lucide-react";
import { animate, type MotionValue, motionValue, useReducedMotion } from "motion/react";
import {
  type CSSProperties,
  type PointerEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { components } from "@/api/schema";
import { Money } from "@/components/Money";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ASPECT, FACE_HUE, loadArt, RADIUS } from "@/lib/cardArt";
import { type Hover, type Pose, stackLayout, stackPoses } from "@/lib/cardPoses";
import type { CardScene, SceneFace } from "@/three/cardScene";

type Account = components["schemas"]["AccountOut"];

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

/** Spring for #59's content transition, so the view below settles with the card. */
export const SELECT_SPRING = { type: "spring", duration: 0.5, bounce: 0.18 } as const;
const HOVER_SPRING = { type: "spring", stiffness: 320, damping: 26, mass: 0.7 } as const;

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
  matches: boolean;
  hue: number | null;
  art: string | null;
};

function faces(accounts: Account[]): Face[] {
  const currency = accounts[0]?.currency ?? "GBP";
  // GBP only for now, like the summary: an All accounts total needs one currency
  const same = accounts.every((a) => a.currency === currency);
  const all: Face = {
    key: "all",
    id: undefined,
    title: "All accounts",
    balance: same
      ? { minor: accounts.reduce((n, a) => n + a.bank_balance.minor, 0), currency }
      : null,
    matches: accounts.every((a) => a.gaps.length === 0),
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
      matches: a.gaps.length === 0,
      hue: accountHue(a, i),
      art: a.art,
    })),
  ];
}

const webgl = () => typeof WebGLRenderingContext !== "undefined";
const transform = (p: Pose) =>
  `translate3d(${p.x}px, ${p.y}px, ${p.z}px) rotateX(${p.rx}deg) rotateY(${p.ry}deg) rotateZ(${p.rz}deg) scale(${p.s})`;

type Springs = Record<Exclude<keyof Pose, "layer">, MotionValue<number>>;
const KEYS = ["x", "y", "z", "rx", "ry", "rz", "s"] as const;

export function AccountStack({
  accounts,
  selected,
  onSelect,
}: {
  accounts: Account[];
  selected?: number;
  onSelect: (id: number | undefined) => void;
}) {
  const reduce = useReducedMotion() ?? false;
  const all = useMemo(() => faces(accounts), [accounts]);
  const [hover, setHover] = useState<Hover | null>(null);
  const [rising, setRising] = useState<number | null>(null);
  const [width, setWidth] = useState(0);
  const [art, setArt] = useState<Record<string, HTMLCanvasElement>>({});
  const [gl, setGl] = useState(false);
  const stack = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const scene = useRef<CardScene | null>(null);
  const onScreen = useRef(true); // the scene skips frames while the stack is off screen

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

  // One set of springs per card; the DOM and the WebGL scene both read them.
  const springs = useMemo<Springs[]>(
    () =>
      all.map(() => Object.fromEntries(KEYS.map((k) => [k, motionValue(k === "s" ? 1 : 0)])) as Springs),
    [all],
  );
  const light = useMemo(() => ({ x: motionValue(0), y: motionValue(0), on: motionValue(0) }), []);

  // Draw: write each card's transform and render the scene, at most once a frame.
  const draw = useRef((_scene = true) => {});
  draw.current = (withScene = true) => {
    const now = springs.map((s, i) => ({
      ...(Object.fromEntries(KEYS.map((k) => [k, s[k].get()])) as Omit<Pose, "layer">),
      layer: poses[i]?.layer ?? 0,
    }));
    now.forEach((p, i) => {
      const el = buttons.current[i];
      if (el) el.style.transform = transform(p);
    });
    if (withScene && scene.current && onScreen.current && !document.hidden) {
      scene.current.update(
        now,
        { x: light.x.get(), y: light.y.get(), on: light.on.get() },
        bands.map((top, i) => (top === null ? null : { top, front: isFront(i) })),
      );
      scene.current.render();
    }
  };
  useEffect(() => {
    let queued = 0;
    const schedule = () => {
      if (!queued) queued = requestAnimationFrame(() => ((queued = 0), draw.current()));
    };
    const offs = [...springs.flatMap((s) => KEYS.map((k) => s[k].on("change", schedule))), light.x.on("change", schedule), light.y.on("change", schedule), light.on.on("change", schedule)];
    document.addEventListener("visibilitychange", schedule);
    return () => {
      for (const off of offs) off();
      document.removeEventListener("visibilitychange", schedule);
      cancelAnimationFrame(queued);
    };
  }, [springs, light]);

  // Move every spring towards this render's poses (instantly under reduced motion).
  const placed = useRef<Springs[] | null>(null);
  useLayoutEffect(() => {
    const first = placed.current !== springs; // new cards start where they belong
    placed.current = springs;
    poses.forEach((p, i) => {
      const s = springs[i];
      if (!s) return;
      const chosen = i === frontIndex || i === rising;
      for (const k of KEYS) {
        if (first || reduce) s[k].jump(p[k]);
        else animate(s[k], p[k], chosen && k !== "rx" && k !== "ry" ? SELECT_SPRING : HOVER_SPRING);
      }
    });
    const lit = hover && !reduce ? hover : null;
    const target = lit ? poses[lit.index] : undefined;
    animate(light.on, lit ? 1 : 0, { duration: 0.2 });
    const litFace = lit ? all[lit.index] : undefined;
    if (lit && target && litFace) {
      animate(light.x, lit.px * width + target.x, HOVER_SPRING);
      animate(light.y, lit.py * heightOf(litFace) + target.y, HOVER_SPRING);
    }
    draw.current(false); // DOM only: undo React's write of the target transform; the frame renders the scene
  });

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

  // The user's own art, from the local folder through the API; null keeps the generic face.
  useEffect(() => {
    let live = true;
    for (const key of new Set(all.flatMap((f) => (f.art ? [f.art] : []))))
      void loadArt(key).then((c) => {
        if (live && c) setArt((a) => ({ ...a, [key]: c }));
      });
    return () => {
      live = false;
    };
  }, [all]);

  // WebGL: one lazy-loaded canvas for the whole stack, never under reduced motion.
  const wantGl = !reduce && webgl() && width > 0;
  useEffect(() => {
    if (!wantGl || !canvas.current) return;
    let live = true;
    const el = canvas.current;
    const lost = (e: Event) => {
      e.preventDefault();
      // shortcut: no webglcontextrestored path, so the CSS cards stay until remount.
      const s = scene.current;
      scene.current = null; // before dispose: its forceContextLoss fires this event again
      s?.dispose();
      setGl(false);
    };
    el.addEventListener("webglcontextlost", lost);
    import("@/three/cardScene")
      .then(({ createCardScene }) => {
        if (!live) return;
        scene.current = createCardScene(el, PERSPECTIVE);
        setGl(true);
      })
      .catch(() => setGl(false));
    return () => {
      live = false;
      el.removeEventListener("webglcontextlost", lost);
      scene.current?.dispose();
      scene.current = null;
      setGl(false);
    };
  }, [wantGl]);

  const allH = layout.allH;
  const sceneFaces = useMemo<SceneFace[]>(
    () =>
      all.map((f) => ({ art: (f.art && art[f.art]) || null, hue: f.hue, h: f.id === undefined ? allH : cardH })),
    [all, art, allH, cardH],
  );
  const stackH = layout.height;
  useEffect(() => {
    const s = scene.current;
    if (!gl || !s) return;
    s.setSize(width, stackH, MX, MY);
    s.setFaces(sceneFaces);
    draw.current();
  }, [gl, width, stackH, sceneFaces]);

  // Pause while the stack is off screen: the scene only renders when something moves.
  useEffect(() => {
    const el = stack.current;
    if (!gl || !el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([e]) => {
      onScreen.current = e?.isIntersecting ?? true;
      if (onScreen.current) draw.current();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [gl]);

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
              onClick={() => {
                if (!on) setRising(i);
                onSelect(f.id);
              }}
              onPointerMove={point(i)}
              onPointerLeave={() => setHover((h) => (h?.index === i ? null : h))}
              onFocus={(e) => {
                if (e.currentTarget.matches(":focus-visible")) setHover({ index: i, px: 0.5, py: 0.5 });
              }}
              onBlur={() => setHover((h) => (h?.index === i ? null : h))}
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
                            : `linear-gradient(to bottom, transparent ${b}px, rgb(6 8 10 / ${isFront(i) ? 0.88 : 0.72}) ${b + (isFront(i) ? 44 : 28)}px), ${image}`,
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
                <Money value={f.balance} className="account-balance text-row font-semibold" />
              ) : null}
              <span data-slot="status" className="flex items-center gap-1.5 text-label font-semibold">
                {f.matches ? (
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
        <ul className="flex flex-col gap-2 text-label text-muted-foreground">
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
