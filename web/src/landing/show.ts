// The landing's canned show, transcribed verbatim from `landing.js`.
//
// The three hero searches are v1's canned set, verified against the live
// corpus (research/demo-queries-2026-08-09.md); the frames and boxes were read
// off the box. Beat 3's wall is v1's 70-keyframe monitor-wall set, ids and
// timecodes verbatim.

import { GRID, type GridVideo } from "./corpus";

/** One box drawn on a frame: normalised [x0, y0, x1, y1], with its tab. */
export type Box = {
  b: [number, number, number, number];
  on?: boolean;
  tab?: string;
  conf?: string;
};

export type CannedQuery = {
  label: string;
  q: string;
  vid: string;
  img: string;
  t: number;
  tc: string;
  at: string;
  mode: string;
  conf: string;
  counts: string;
  seen: string;
  boxes: Box[];
  said: string;
  saidTc: string;
  who: string;
  talk: string;
};

export const QUERIES: CannedQuery[] = [
  {
    label: "owl:FunctionalProperty",
    q: "owl:FunctionalProperty",
    vid: "Sir59K8ZDPU",
    img: "pgm/r1.jpg",
    t: 1136,
    tc: "00:18:56",
    at: "18:56",
    mode: "ocr · exact",
    conf: "1.00",
    counts: "15 frames · 35 cues · 3 videos",
    seen: "owl:FunctionalProperty · line 11 of 34 · conf 1.00",
    boxes: [{ b: [0.499, 0.308, 0.647, 0.338], on: true, tab: "on-screen text", conf: "1.00" }],
    said: "So you have these functional properties, disjoint properties … the errors it can catch, look over in the right-hand column.",
    saidTc: "00:19:01",
    who: "Frank Coyle",
    talk: "Why Agentic Systems Need Ontologies",
  },
  {
    label: "the sharded-mongo diagram",
    q: "the talk where they showed the sharded-mongo diagram",
    vid: "lyL5QhgIOxc",
    img: "pgm/r2.jpg",
    t: 1022,
    tc: "00:17:02",
    at: "17:02",
    mode: "frame + transcript · semantic",
    conf: "0.98",
    counts: "11 frames · 37 cues · 5 videos",
    seen: "MONGOS · CONFIG SERVERS · SHARD A · SHARD B · 6 of 41 lines",
    boxes: [
      { b: [0.395, 0.19, 0.455, 0.221], on: true, tab: "mongos" },
      { b: [0.534, 0.19, 0.594, 0.221], on: true },
      { b: [0.668, 0.19, 0.726, 0.221], on: true },
      { b: [0.832, 0.192, 0.927, 0.221], on: true, tab: "config servers" },
      { b: [0.275, 0.414, 0.34, 0.443], on: true, tab: "shard a" },
      { b: [0.684, 0.414, 0.747, 0.443], on: true, tab: "shard b" },
    ],
    said: "Sharding means scaling your database horizontally.",
    saidTc: "00:16:56",
    who: "Arek Borucki",
    talk: "Serving 2 Million Models Without Melting",
  },
  {
    label: "the most expensive typo",
    q: "the most expensive typo in history",
    vid: "tJFjeMBKbIY",
    img: "pgm/r3.jpg",
    t: 456,
    tc: "00:07:36",
    at: "07:36",
    mode: "transcript · lexical",
    conf: "1.00",
    counts: "10 frames · 48 cues · 7 videos",
    seen: "~$100,000,000,000 · line 4 of 14",
    boxes: [{ b: [0.38, 0.261, 0.827, 0.376], on: true, tab: "on-screen text" }],
    said: "Let me tell you about the most expensive typo in history.",
    saidTc: "00:07:24",
    who: "Shawn Chan",
    talk: "Build for the Memo, Not the Demo",
  },
];

/**
 * The hero wall's tile order: the three findable talks are slotted in so they
 * are on the wall even when the viewport only fits a few dozen tiles.
 */
export const WALL_ORDER: GridVideo[] = (() => {
  const rest = GRID.filter((g) => !QUERIES.some((q) => q.vid === g.vid));
  const picks = QUERIES.map((q) => GRID.find((g) => g.vid === q.vid)!);
  const out = [...rest];
  out.splice(7, 0, picks[0]);
  out.splice(19, 0, picks[1]);
  out.splice(31, 0, picks[2]);
  return out;
})();

/** Beat 3's wall band: [file, video id, timecode], v1's set, verbatim. */
export const TILES: [string, string, string][] = [
  ["t00.jpg", "CoEIs6Xm8m8", "00:00:45"],
  ["t01.jpg", "z0sh8HyTrDo", "00:02:55"],
  ["t02.jpg", "b_PmGocP4rc", "00:01:12"],
  ["t03.jpg", "hacEQHHhu2Q", "00:00:35"],
  ["t04.jpg", "s67bE2Ur3bY", "00:00:40"],
  ["t05.jpg", "Byv311hdoHE", "00:00:52"],
  ["t06.jpg", "tJFjeMBKbIY", "00:00:35"],
  ["t07.jpg", "O-CBZ3JtRvo", "00:05:10"],
  ["t08.jpg", "o6U_2vd967Y", "00:03:33"],
  ["t09.jpg", "tJFjeMBKbIY", "00:07:36"],
  ["t10.jpg", "zkX03APVj0M", "00:02:26"],
  ["t11.jpg", "zkX03APVj0M", "00:01:10"],
  ["t12.jpg", "cJ0EOzey--o", "00:00:52"],
  ["t13.jpg", "-jY2T2PiJBE", "00:00:17"],
  ["t14.jpg", "jRCpXUjz4CI", "00:00:55"],
  ["t15.jpg", "CgsWxRUY5Eo", "00:12:46"],
  ["t16.jpg", "AMiyLItEtLA", "00:08:41"],
  ["t17.jpg", "Byv311hdoHE", "00:05:58"],
  ["t18.jpg", "jWq-aZIU0kM", "00:05:54"],
  ["t19.jpg", "ZFxh7sqbUZo", "00:24:35"],
  ["t20.jpg", "Ib5GBkD555M", "00:00:18"],
  ["t21.jpg", "q2JrUKBMf0w", "00:05:45"],
  ["t22.jpg", "GgLQ02aO-hs", "00:00:44"],
  ["t23.jpg", "QHBjufYK8TA", "00:10:43"],
  ["t24.jpg", "FWMJQDH3iK0", "00:08:00"],
  ["t25.jpg", "J4_jCrTxMkk", "00:28:04"],
  ["t26.jpg", "2JX6JYyQG4Y", "00:00:28"],
  ["t27.jpg", "GgLQ02aO-hs", "00:00:37"],
  ["t28.jpg", "2JX6JYyQG4Y", "00:00:41"],
  ["t29.jpg", "jQDXzEVHMSE", "00:00:23"],
  ["t30.jpg", "J4_jCrTxMkk", "00:28:37"],
  ["t31.jpg", "jQDXzEVHMSE", "00:00:19"],
  ["t32.jpg", "2JX6JYyQG4Y", "00:00:32"],
  ["t33.jpg", "AMiyLItEtLA", "00:12:23"],
  ["t34.jpg", "z0sh8HyTrDo", "00:04:41"],
  ["t35.jpg", "jRCpXUjz4CI", "00:18:34"],
  ["t36.jpg", "xIt_mTQp6mY", "00:16:34"],
  ["t37.jpg", "jRCpXUjz4CI", "00:06:38"],
  ["t38.jpg", "LZuWZRze3MU", "00:18:28"],
  ["t39.jpg", "KhYifX22yhE", "00:17:04"],
  ["t40.jpg", "0RNNfxpdbQk", "00:00:11"],
  ["t41.jpg", "1EZdpEhwmNc", "00:07:05"],
  ["t42.jpg", "LZuWZRze3MU", "00:10:47"],
  ["t43.jpg", "pWXUkLP9uWM", "00:02:25"],
  ["t44.jpg", "xIt_mTQp6mY", "00:04:36"],
  ["t45.jpg", "Sir59K8ZDPU", "00:18:56"],
  ["t46.jpg", "zkX03APVj0M", "00:10:37"],
  ["t47.jpg", "418t26CVz-w", "00:05:55"],
  ["t48.jpg", "o6U_2vd967Y", "00:18:14"],
  ["t49.jpg", "ZyIoTOAbRfs", "00:11:41"],
  ["t50.jpg", "3ZMUiFaQ3qg", "00:11:46"],
  ["t51.jpg", "Yk87oUPVaxU", "00:04:27"],
  ["t52.jpg", "AMiyLItEtLA", "00:01:48"],
  ["t53.jpg", "-I5W5QVAT8E", "00:00:40"],
  ["t54.jpg", "LZuWZRze3MU", "00:09:23"],
  ["t55.jpg", "k35LeKZEhiE", "00:17:35"],
  ["t56.jpg", "RVxym6mmIns", "00:11:08"],
  ["t57.jpg", "s4r6nk5WsZw", "00:05:47"],
  ["t58.jpg", "-jY2T2PiJBE", "00:03:33"],
  ["t59.jpg", "GgLQ02aO-hs", "00:00:34"],
  ["t60.jpg", "Sir59K8ZDPU", "00:20:26"],
  ["t61.jpg", "Sir59K8ZDPU", "00:20:10"],
  ["t62.jpg", "Sir59K8ZDPU", "00:09:32"],
  ["t63.jpg", "Sir59K8ZDPU", "00:00:23"],
  ["t64.jpg", "lyL5QhgIOxc", "00:17:02"],
  ["t65.jpg", "lyL5QhgIOxc", "00:11:22"],
  ["t66.jpg", "lyL5QhgIOxc", "00:00:40"],
  ["t67.jpg", "lyL5QhgIOxc", "00:05:56"],
  ["t68.jpg", "pWXUkLP9uWM", "00:18:48"],
  ["t69.jpg", "pWXUkLP9uWM", "00:02:54"],
];

/** Beat 2's plates, in the order the page lays them out. */
export const PLATE_IDS = [3285, 41, 1944, 564];

/**
 * Tab labels for beat 2's boxes, by moment id then by index into its `ocr`.
 * The confidence beside each is read off the OCR row itself.
 */
export const PLATE_TABS: Record<number, Record<number, string>> = {
  3285: { 5: "on-screen text", 9: "never spoken" },
  41: { 2: "on-screen text" },
  1944: { 10: "the error, on screen" },
};

/** Beat 4's question, typed in when the booth log is reached. */
export const BOOTH_QUESTION = "What do speakers disagree about when it comes to LLM as a judge?";
