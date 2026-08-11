(() => {
  'use strict';
  /* Root-absolute, not relative: the document is served at `/` and every
     `src` in here resolves against *it*, not against this file. */
  const D = window.VT, A = '/static/landing/';
  const M = Object.fromEntries(D.moments.map(m => [m.id, m]));
  const $ = s => document.querySelector(s);
  const el = (t, c, h) => { const n = document.createElement(t); if (c) n.className = c;
    if (h != null) n.innerHTML = h; return n; };
  const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const pad = n => String(n).padStart(2, '0');
  const hms = s => { s = Math.floor(s); const h = (s/3600)|0, m = ((s%3600)/60)|0;
    return (h ? h + ':' + pad(m) : pad(m)) + ':' + pad(s % 60); };
  const num = n => Math.round(n).toLocaleString('en-US');
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const ARROW = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square"><path d="M3.4 8.6 8.6 3.4M4.6 3.4h4v4"/></svg>';

  /* ?still=1 is the lab's stills switch: the page paints its end state at
     once, which is how it is judged from screenshots. Same path as reduced
     motion — the motion law's off switch. */
  const STILL = location.search.includes('still');
  const reduce = STILL || matchMedia('(prefers-reduced-motion: reduce)').matches;

  const receipt = (vid, t, cls = '') =>
    `<a class="receipt ${cls}" href="https://youtu.be/${vid}?t=${Math.floor(t)}" rel="noopener">` +
    `<span>youtu.be/</span><span class="vid id">${vid}</span>` +
    `<b>?t=${Math.floor(t)}${ARROW}</b></a>`;

  /* the frame with its detections, boxes exactly where ocr_lines put them */
  function frame(img, alt, boxes, opts = {}) {
    const f = el('figure', 'frame' + (opts.acq ? ' acq' : ''));
    const im = el('img');
    im.src = img; im.loading = 'eager'; im.decoding = 'async'; im.alt = alt;
    f.append(im);
    boxes.forEach((b, i) => {
      const d = el('div', 'det' + (b.on ? ' on' : ''));
      d.style.cssText = `--x:${(b.b[0]*100).toFixed(2)}%;--y:${(b.b[1]*100).toFixed(2)}%;` +
        `--w:${((b.b[2]-b.b[0])*100).toFixed(2)}%;--h:${((b.b[3]-b.b[1])*100).toFixed(2)}%;--i:${i}`;
      if (b.tab) d.append(el('span', 'tab', b.tab));
      f.append(d);
    });
    return f;
  }

  /* ═══ the three canned searches — v1's verified set, data verbatim ═══
     (research/demo-queries-2026-08-09.md; frames and boxes off the box) */
  const QUERIES = [
    { label:'owl:FunctionalProperty', q:'owl:FunctionalProperty',
      vid:'Sir59K8ZDPU', img:A+'pgm/r1.jpg', t:1136, tc:'00:18:56', at:'18:56',
      mode:'ocr · exact', conf:'1.00', counts:'15 frames · 35 cues · 3 videos',
      seen:'owl:FunctionalProperty · line 11 of 34 · conf 1.00',
      boxes:[{b:[0.499,0.308,0.647,0.338],on:true,tab:'on-screen text<i>1.00</i>'}],
      said:'So you have these functional properties, disjoint properties … the errors it can catch, look over in the right-hand column.',
      saidTc:'00:19:01', who:'Frank Coyle', talk:'Why Agentic Systems Need Ontologies' },
    { label:'the sharded-mongo diagram', q:'the talk where they showed the sharded-mongo diagram',
      vid:'lyL5QhgIOxc', img:A+'pgm/r2.jpg', t:1022, tc:'00:17:02', at:'17:02',
      mode:'frame + transcript · semantic', conf:'0.98', counts:'11 frames · 37 cues · 5 videos',
      seen:'MONGOS · CONFIG SERVERS · SHARD A · SHARD B · 6 of 41 lines',
      boxes:[{b:[0.395,0.190,0.455,0.221],on:true,tab:'mongos'},
             {b:[0.534,0.190,0.594,0.221],on:true},
             {b:[0.668,0.190,0.726,0.221],on:true},
             {b:[0.832,0.192,0.927,0.221],on:true,tab:'config servers'},
             {b:[0.275,0.414,0.340,0.443],on:true,tab:'shard a'},
             {b:[0.684,0.414,0.747,0.443],on:true,tab:'shard b'}],
      said:'Sharding means scaling your database horizontally.',
      saidTc:'00:16:56', who:'Arek Borucki', talk:'Serving 2 Million Models Without Melting' },
    { label:'the most expensive typo', q:'the most expensive typo in history',
      vid:'tJFjeMBKbIY', img:A+'pgm/r3.jpg', t:456, tc:'00:07:36', at:'07:36',
      mode:'transcript · lexical', conf:'1.00', counts:'10 frames · 48 cues · 7 videos',
      seen:'~$100,000,000,000 · line 4 of 14',
      boxes:[{b:[0.380,0.261,0.827,0.376],on:true,tab:'on-screen text'}],
      said:'Let me tell you about the most expensive typo in history.',
      saidTc:'00:07:24', who:'Shawn Chan', talk:'Build for the Memo, Not the Demo' }
  ];

  /* ═══ beat 1 — the wall, built to fill the room ═══ */
  const hero = $('#top'), wallEl = $('#heroWall'), wallWrap = document.querySelector('.wallwrap');
  /* order the wall so the three findable talks are on it even when the
     viewport only fits a few dozen tiles */
  const wallOrder = (() => {
    const rest = D.grid.filter(g => !QUERIES.some(q => q.vid === g.vid));
    const picks = QUERIES.map(q => D.grid.find(g => g.vid === q.vid));
    const out = [...rest];
    out.splice(7, 0, picks[0]); out.splice(19, 0, picks[1]); out.splice(31, 0, picks[2]);
    return out;
  })();
  let currentQ = -1;
  const heroin = document.querySelector('.heroin');
  function buildWall() {
    const w = innerWidth;
    /* fewer, bigger frames — v1's wall pitch rather than v3's mosaic
       (Tom, 2026-08-10). One tile is ~145 CSS px of viewport. */
    const cols = Math.max(4, Math.min(12, Math.round(w / 145)));
    const tw = (w * 1.10) / cols, th = tw * 9 / 16;
    /* stacked layouts get a clear lane of exactly one tile plus air between
       the copy and the light table — measured here, spent by the CSS */
    heroin.style.setProperty('--lane', Math.round(th + 58) + 'px');
    const h = hero.offsetHeight || innerHeight;   /* read after the lane is set */
    const rows = Math.max(4, Math.ceil((h * 1.10) / th) + 1);
    wallEl.style.gridTemplateColumns = `repeat(${cols},1fr)`;
    /* rows are pinned to the tile height rather than stretched to 1fr: the
       frames stay 16:9 (they are frames), and the clearance arithmetic that
       places the found tile is then exact. The wrap clips the remainder. */
    wallEl.style.gridAutoRows = th.toFixed(2) + 'px';
    wallEl.innerHTML = '';
    const n = cols * rows;
    for (let i = 0; i < n; i++) {
      const g = wallOrder[i % wallOrder.length];
      const d = el('div', 'wt');
      d.dataset.vid = g.vid;
      d.innerHTML = `<img src="${A}${g.img}" alt="" loading="eager" decoding="async">`;
      wallEl.append(d);
    }
    if (currentQ >= 0) {
      wallEl.classList.add('searching');
      const t = pickTile(QUERIES[currentQ].vid);
      if (t) t.classList.add('hit');
    }
  }
  /* ─── where the found frame is allowed to light up ───────────────────
     Tom's note, 2026-08-10: "the frame should be near the component on the
     right, but not overlapping it — 'the most expensive typo' looks the
     best." So the pick is geometry, not luck: the lit tile must clear the
     light table AND every actual line of hero text (line boxes, not the
     copy column's bounding box, which is mostly empty air on its right),
     and among the tiles that clear, the one nearest the table wins. All
     three queries therefore land in the same place-relative-to-the-table.
     Clearance is measured with a margin that also covers the whole drift
     cycle, so nothing creeps into the copy while you watch. */
  function obstacles() {
    const out = [];
    /* real line boxes for running text — a block's own rect is mostly empty
       air on its right, and that air is where the found frame belongs */
    const push = s => { const n = document.querySelector(s); if (!n) return;
      const rg = document.createRange(); rg.selectNodeContents(n);
      for (const r of rg.getClientRects()) if (r.width > 1 && r.height > 1) out.push(r); };
    ['.herocopy .kick', '.herocopy h1', '.herocopy .lede'].forEach(push);
    ['#slug', '#askbtn', '#chips', '#bench', '.rail', '.herofoot']
      .forEach(s => { const n = document.querySelector(s);
        if (n) out.push(n.getBoundingClientRect()); });
    return out;
  }
  const gapTo = (a, b) => {                       /* 0 when the rects touch/overlap */
    const dx = Math.max(b.left - a.right, a.left - b.right, 0);
    const dy = Math.max(b.top - a.bottom, a.top - b.bottom, 0);
    return Math.hypot(dx, dy);
  };
  const over = (a, b, m) => a.left < b.right + m && a.right > b.left - m &&
                            a.top < b.bottom + m && a.bottom > b.top - m;
  function pickTile(vid) {
    const tiles = [...wallEl.querySelectorAll('.wt')];
    if (!tiles.length) return null;
    const hr = hero.getBoundingClientRect();
    const benchR = document.querySelector('#bench').getBoundingClientRect();
    const obs = obstacles();
    /* a tile that hangs off the room, or off the viewport, is half a frame:
       the found frame has to be whole before it can lift */
    const inHero = r => r.top >= hr.top - 1 && r.bottom <= hr.bottom + 1 &&
      r.left >= -1 && r.right <= innerWidth + 1;
    /* the wall drifts: budget for the worst position in the cycle so the
       clearance Tom sees in a screenshot is the clearance at every moment */
    const DRIFT = 14, AIR = 10;
    const air = r => Math.min(...obs.map(o => gapTo(r, o)));
    let pool = tiles.filter(t => {
      const r = t.getBoundingClientRect();
      return inHero(r) && !obs.some(o => over(r, o, AIR + DRIFT));
    });
    /* nothing is fully clear (a very short viewport): never touch the table,
       and take the tile that keeps the most air */
    if (!pool.length) {
      let rest = tiles.filter(t => { const r = t.getBoundingClientRect();
        return inHero(r) && !over(r, benchR, DRIFT); });
      if (!rest.length) rest = tiles;
      pool = [rest.reduce((a, b) =>
        air(b.getBoundingClientRect()) > air(a.getBoundingClientRect()) ? b : a)];
    }
    /* nearest to the table wins — that is the whole point of the note — and
       near-ties go to whichever of them keeps the most air around it */
    const dist = t => gapTo(t.getBoundingClientRect(), benchR);
    pool.sort((a, b) => dist(a) - dist(b) ||
      air(b.getBoundingClientRect()) - air(a.getBoundingClientRect()));
    const bd = dist(pool[0]);
    const near = pool.filter(t => dist(t) <= bd + 40);
    const best = near.reduce((a, b) =>
      air(b.getBoundingClientRect()) > air(a.getBoundingClientRect()) ? b : a);
    /* a tile that already shows this talk wins ties generously, so the wall
       lights one of its own frames when it can */
    const own = pool.find(t => t.dataset.vid === vid && dist(t) <= dist(best) * 1.3 + 40);
    if (own) return own;
    /* every copy of that talk sits under the copy or the table: re-slot its
       cover onto the chosen tile so the find is visible on the wall */
    const g = D.grid.find(x => x.vid === vid);
    if (g) { best.dataset.vid = vid; best.querySelector('img').src = A + g.img; }
    return best;
  }
  buildWall();
  if (reduce) wallWrap.style.animation = 'none';   /* ?still=1 freezes the gate too */
  let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(buildWall, 200); }, { passive: true });

  /* the light table */
  const bench = $('#bench');
  function populateBench(q, animate) {
    $('#benchhead').innerHTML =
      `<span class="ml gold">lifted off the wall</span>
       <span class="ml seen">seen — ${esc(q.seen)}</span>
       <span class="ml r hide-s">src <span class="id">${q.vid}</span> · tc ${q.tc}</span>`;
    const nf = frame(q.img,
      `Matched keyframe at ${q.at} of ${q.talk} — ${q.who}`, q.boxes, { acq: animate });
    bench.querySelector('.frame').replaceWith(nf);
    nf.style.opacity = '1';
    const say = $('#benchsay'); say.hidden = false;
    say.innerHTML =
      `<span class="ml gold">heard — spoken at ${q.saidTc}</span>
       <p class="said">${esc(q.said)}</p>
       <div class="who"><strong>${esc(q.who)}</strong><em>${esc(q.talk)}</em></div>`;
    const foot = $('#benchfoot'); foot.hidden = false;
    foot.innerHTML = `${receipt(q.vid, q.t)}<span class="ml r hide-s">${q.mode} · ${q.counts}</span>`;
    bench.classList.remove('idle');
  }

  /* the ask cycle: type → scan → lift → land */
  const qtext = $('#qtext'), slug = $('#slug'), statusEl = $('#status');
  const setStatus = (s, label) => { statusEl.dataset.s = s; statusEl.textContent = label; };
  let token = 0;
  async function typeIn(text, tk) {
    slug.classList.add('typing'); qtext.textContent = '';
    for (let i = 0; i < text.length; i++) {
      if (tk !== token) return false;
      qtext.textContent = text.slice(0, i + 1);
      await sleep(30 + (text[i] === ' ' ? 16 : 0));
    }
    slug.classList.remove('typing');
    return true;
  }
  function chips(active) {
    $('#chips').innerHTML = QUERIES.map((q, j) =>
      `<button type="button" data-i="${j}" class="${j === active ? 'on' : ''}">${esc(q.label)}</button>`).join('');
  }
  function paintFinal(i) {
    const q = QUERIES[i];
    qtext.textContent = q.q;
    slug.classList.remove('typing');
    wallEl.classList.add('searching');
    wallEl.querySelectorAll('.wt.hit').forEach(t => t.classList.remove('hit'));
    populateBench(q, false);           /* final geometry first, then pick */
    const t = pickTile(q.vid); if (t) t.classList.add('hit');
    setStatus('lifted', 'lifted · ' + q.at);
    chips(i); currentQ = i;
  }
  async function run(i) {
    const q = QUERIES[i]; const tk = ++token;
    chips(i); currentQ = i;
    if (reduce) { paintFinal(i); return; }
    /* reset */
    wallEl.classList.remove('searching');
    wallEl.querySelectorAll('.wt.hit').forEach(t => t.classList.remove('hit'));
    bench.classList.add('idle');
    new Image().src = q.img;                    /* warm the still */
    setStatus('reading', 'reading');
    if (!await typeIn(q.q, tk)) return;
    if (tk !== token) return;
    setStatus('scanning', 'scanning the wall');
    wallEl.classList.add('searching');
    /* lay the table out at its final size (veiled) so the pick and the
       lift's landing rect are measured against where things will be */
    populateBench(q, false); bench.classList.add('veil'); bench.classList.add('idle');
    await sleep(460); if (tk !== token) return;
    const tile = pickTile(q.vid);
    if (tile) tile.classList.add('hit');
    await sleep(480); if (tk !== token) return;
    /* the lift: the matched keyframe comes off the talk's tile and lands
       on the table */
    if (tile) {
      const from = tile.getBoundingClientRect();
      const to = bench.querySelector('.frame').getBoundingClientRect();
      /* both rects are 16:9, so the flight is one composited transform:
         the element is laid out at the landing size and scaled down onto
         the tile. Nothing re-lays-out mid-flight, so nothing jitters. */
      const s = from.width / to.width;
      const dx = from.left - to.left, dy = from.top - to.top;
      const lift = el('div', 'lift', `<img src="${q.img}" alt="">`);
      lift.style.cssText =
        `left:${to.left}px;top:${to.top}px;width:${to.width}px;height:${to.height}px;` +
        `outline-width:${(2 / s).toFixed(2)}px;` +
        `transform:translate3d(${dx}px,${dy}px,0) scale(${s})`;
      document.body.append(lift);
      /* beat one: it comes off the wall — a short, visible detach before the
         travel, so the lift reads as a lift and not as a cut */
      await sleep(40); if (tk !== token) { lift.remove(); return; }
      lift.style.transition = 'transform .24s ease-out,outline-width .24s ease-out';
      lift.style.transform =
        `translate3d(${dx - from.width * .04}px,${dy - from.height * .04}px,0) scale(${s * 1.08})`;
      await sleep(270); if (tk !== token) { lift.remove(); return; }
      /* beat two: the travel, on the stylesheet's .82s ease */
      lift.style.transition = '';
      lift.style.transform = 'translate3d(0,0,0) scale(1)';
      lift.style.outlineWidth = '2px';
      await sleep(860); if (tk !== token) { lift.remove(); return; }
      populateBench(q, true); bench.classList.remove('veil');
      await sleep(60); lift.remove();
    } else {
      populateBench(q, true); bench.classList.remove('veil');
    }
    setStatus('lifted', 'lifted · ' + q.at);
  }
  chips(-1);
  $('#chips').addEventListener('click', e => {
    const b = e.target.closest('button[data-i]'); if (b) run(+b.dataset.i);
  });
  /* `#askbtn` is a link to `/demo` now, not a control on this cycle: the canned
     hero is the landing's own show and the CTA is the way *out* of it, into the
     live corpus. The chips still drive the cycle; nothing else changed, and the
     element keeps its id and its box because `obstacles()` measures it. */
  /* the room is already working when you walk in */
  if (reduce) paintFinal(0); else setTimeout(() => { if (currentQ < 0) run(0); }, 700);

  document.getElementById('railCorpus').innerHTML =
    'following <span class="id gold">' + esc(D.stats.channel) + '</span> · ' +
    num(D.stats.talks) + ' talks watched';

  /* ═══ beat 2 — the bench of stills (real moments, data.js) ═══ */
  {
    /* tab labels; the confidence beside each is read off the OCR row itself */
    const tabsFor = {
      3285: { 5: 'on-screen text', 9: 'never spoken' },
      41:   { 2: 'on-screen text' },
      1944: { 10: 'the error, on screen' }
    };
    const plates = document.getElementById('plates');
    [3285, 41, 1944, 564].forEach(id => {
      const m = M[id], cue = m.cues[m.cue_index];
      const p = el('div', 'plate');
      p.append(el('span', 'vlab',
        `still <u>nº${String(m.id).padStart(4,'0')}</u> · ord ${m.ord} · ${hms(m.t)} of ${hms(m.duration)}`));
      const th = el('div', 'th');
      const boxes = m.ocr.map((l, i) => {
        const lab = (tabsFor[id] || {})[i];
        return { b: l.b, on: m.hero.includes(i),
          tab: lab ? lab + '<i>' + l.c.toFixed(2) + '</i>' : undefined };
      });
      th.append(frame(A + m.img,
        'Keyframe at ' + hms(m.t) + ' of ' + m.title + ' — ' + m.speaker, boxes));
      p.append(th);
      const seen = m.ocr.length
        ? `<div class="seenline"><span class="ll">seen</span><p>${esc(m.hero.map(i => m.ocr[i].t).join(' '))}</p></div>`
        : `<p class="note">nothing readable on screen here — <b>ocr_state=empty</b>, so this
             moment stands on the sentence alone, and the frame is the evidence.</p>`;
      p.append(el('div', 'bd',
        `<div class="who"><strong>${esc(m.speaker)}</strong><em>${esc(m.title)}${m.org ? ' · ' + esc(m.org) : ''}</em></div>
         <p class="said">${esc(cue.t)}</p>
         ${seen}
         <div class="mfoot">${receipt(m.video_id, m.t, 'sm')}<span class="ml r">${esc(m.kick)}</span></div>`));
      plates.append(p);
    });
  }

  /* ═══ the ledger — counts up while the machine shows its books ═══ */
  {
    const st = D.stats, hrs = st.seconds / 3600;
    const cells = [
      ['talks', st.talks, v => num(v)],
      ['hours watched', hrs, v => Math.floor(v) + '<u>h</u> ' + pad(Math.round((v % 1) * 60)) + '<u>m</u>'],
      ['moments kept', st.keyframes, v => num(v)],
      ['lines read off the screen', st.ocr_lines, v => num(v)]
    ];
    const ledger = document.getElementById('ledger');
    const dds = cells.map(([k, target, fmt]) => {
      const d = el('div'); const dt = el('dt', '', k); const dd = el('dd', '', fmt(reduce ? target : 0));
      d.append(dt, dd); ledger.append(d);
      return { dd, target, fmt };
    });
    if (!reduce && 'IntersectionObserver' in window) {
      const io = new IntersectionObserver(es => {
        es.forEach(e => {
          if (!e.isIntersecting) return; io.disconnect();
          const t0 = performance.now(), dur = 950;
          const tick = now => {
            const p = Math.min(1, (now - t0) / dur), ease = 1 - (1 - p) ** 3;
            dds.forEach(c => { c.dd.innerHTML = c.fmt(c.target * ease); });
            if (p < 1) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        });
      }, { threshold: .4 });
      io.observe(ledger);
    } else dds.forEach(c => { c.dd.innerHTML = c.fmt(c.target); });
    document.getElementById('ledgerNote').textContent =
      num(st.cues) + ' sentences spoken · ' + num(st.words) + ' words · ' +
      num(st.ocr_frames) + ' frames read';
  }

  /* ═══ beat 3 — the wall: 70 real keyframes, drifting (v4's treatment) ═══
     v1's monitor-wall set, ids and timecodes verbatim. Alternating rows, a
     constant few px/s, frozen under reduced motion and ?still=1. */
  const TILES = [
    ['t00.jpg','CoEIs6Xm8m8','00:00:45'],['t01.jpg','z0sh8HyTrDo','00:02:55'],
    ['t02.jpg','b_PmGocP4rc','00:01:12'],['t03.jpg','hacEQHHhu2Q','00:00:35'],
    ['t04.jpg','s67bE2Ur3bY','00:00:40'],['t05.jpg','Byv311hdoHE','00:00:52'],
    ['t06.jpg','tJFjeMBKbIY','00:00:35'],['t07.jpg','O-CBZ3JtRvo','00:05:10'],
    ['t08.jpg','o6U_2vd967Y','00:03:33'],['t09.jpg','tJFjeMBKbIY','00:07:36'],
    ['t10.jpg','zkX03APVj0M','00:02:26'],['t11.jpg','zkX03APVj0M','00:01:10'],
    ['t12.jpg','cJ0EOzey--o','00:00:52'],['t13.jpg','-jY2T2PiJBE','00:00:17'],
    ['t14.jpg','jRCpXUjz4CI','00:00:55'],['t15.jpg','CgsWxRUY5Eo','00:12:46'],
    ['t16.jpg','AMiyLItEtLA','00:08:41'],['t17.jpg','Byv311hdoHE','00:05:58'],
    ['t18.jpg','jWq-aZIU0kM','00:05:54'],['t19.jpg','ZFxh7sqbUZo','00:24:35'],
    ['t20.jpg','Ib5GBkD555M','00:00:18'],['t21.jpg','q2JrUKBMf0w','00:05:45'],
    ['t22.jpg','GgLQ02aO-hs','00:00:44'],['t23.jpg','QHBjufYK8TA','00:10:43'],
    ['t24.jpg','FWMJQDH3iK0','00:08:00'],['t25.jpg','J4_jCrTxMkk','00:28:04'],
    ['t26.jpg','2JX6JYyQG4Y','00:00:28'],['t27.jpg','GgLQ02aO-hs','00:00:37'],
    ['t28.jpg','2JX6JYyQG4Y','00:00:41'],['t29.jpg','jQDXzEVHMSE','00:00:23'],
    ['t30.jpg','J4_jCrTxMkk','00:28:37'],['t31.jpg','jQDXzEVHMSE','00:00:19'],
    ['t32.jpg','2JX6JYyQG4Y','00:00:32'],['t33.jpg','AMiyLItEtLA','00:12:23'],
    ['t34.jpg','z0sh8HyTrDo','00:04:41'],['t35.jpg','jRCpXUjz4CI','00:18:34'],
    ['t36.jpg','xIt_mTQp6mY','00:16:34'],['t37.jpg','jRCpXUjz4CI','00:06:38'],
    ['t38.jpg','LZuWZRze3MU','00:18:28'],['t39.jpg','KhYifX22yhE','00:17:04'],
    ['t40.jpg','0RNNfxpdbQk','00:00:11'],['t41.jpg','1EZdpEhwmNc','00:07:05'],
    ['t42.jpg','LZuWZRze3MU','00:10:47'],['t43.jpg','pWXUkLP9uWM','00:02:25'],
    ['t44.jpg','xIt_mTQp6mY','00:04:36'],['t45.jpg','Sir59K8ZDPU','00:18:56'],
    ['t46.jpg','zkX03APVj0M','00:10:37'],['t47.jpg','418t26CVz-w','00:05:55'],
    ['t48.jpg','o6U_2vd967Y','00:18:14'],['t49.jpg','ZyIoTOAbRfs','00:11:41'],
    ['t50.jpg','3ZMUiFaQ3qg','00:11:46'],['t51.jpg','Yk87oUPVaxU','00:04:27'],
    ['t52.jpg','AMiyLItEtLA','00:01:48'],['t53.jpg','-I5W5QVAT8E','00:00:40'],
    ['t54.jpg','LZuWZRze3MU','00:09:23'],['t55.jpg','k35LeKZEhiE','00:17:35'],
    ['t56.jpg','RVxym6mmIns','00:11:08'],['t57.jpg','s4r6nk5WsZw','00:05:47'],
    ['t58.jpg','-jY2T2PiJBE','00:03:33'],['t59.jpg','GgLQ02aO-hs','00:00:34'],
    ['t60.jpg','Sir59K8ZDPU','00:20:26'],['t61.jpg','Sir59K8ZDPU','00:20:10'],
    ['t62.jpg','Sir59K8ZDPU','00:09:32'],['t63.jpg','Sir59K8ZDPU','00:00:23'],
    ['t64.jpg','lyL5QhgIOxc','00:17:02'],['t65.jpg','lyL5QhgIOxc','00:11:22'],
    ['t66.jpg','lyL5QhgIOxc','00:00:40'],['t67.jpg','lyL5QhgIOxc','00:05:56'],
    ['t68.jpg','pWXUkLP9uWM','00:18:48'],['t69.jpg','pWXUkLP9uWM','00:02:54']
  ];
  {
    const band = document.getElementById('wallband');
    const byVid = Object.fromEntries(D.grid.map(g => [g.vid, g]));
    const ROWS = 4, rows = Array.from({ length: ROWS }, () => []);
    TILES.forEach((t, i) => rows[i % ROWS].push(t));
    const cell = ([f, id, tc]) => {
      const g = byVid[id];
      const fig = el('figure', 'bwt');
      fig.title = (g ? g.title + ' — ' + g.speaker + (g.org ? ' (' + g.org + ')' : '') +
        ' · ' + hms(g.dur) + ' · ' + num(g.cues) + ' spoken lines · ' +
        num(g.ocr) + ' lines read off the screen · ' : '') + id + ' · ' + tc;
      fig.innerHTML = `<img src="${A}wall/${f}" alt="${esc(g ? g.title + ' — ' + g.speaker : id)}"` +
        ` loading="lazy" decoding="async" width="480" height="270">` +
        `<span class="bslug"><b>${esc(id)}</b><span>${tc}</span></span>`;
      return fig;
    };
    rows.forEach((list, r) => {
      const row = el('div', 'wrow');
      const track = el('div', 'wtrack' + (r % 2 ? ' rev' : ''));
      list.forEach(t => track.append(cell(t)));
      list.forEach(t => track.append(cell(t)));   /* duplicate: seamless loop */
      row.append(track); band.append(row);
    });
    /* the drift speed is a constant few px/s, so duration follows width */
    const SPEED = 3.2; /* px per second */
    const pace = () => band.querySelectorAll('.wtrack').forEach(tr => {
      if (reduce) { tr.style.animation = 'none'; return; }
      const half = tr.scrollWidth / 2;
      tr.style.setProperty('--shift', half + 'px');
      tr.style.animationDuration = (half / SPEED).toFixed(0) + 's';
    });
    requestAnimationFrame(pace);
    let bt; addEventListener('resize', () => { clearTimeout(bt); bt = setTimeout(pace, 200); },
      { passive: true });

    const st = D.stats;
    document.getElementById('bandLine').innerHTML =
      `${num(st.talks)} talks · ${num(st.cues)} sentences spoken · ${num(st.words)} words · ` +
      `${num(st.ocr_frames)} frames read · one channel followed so far — ` +
      `<span class="gold">${esc(st.channel)}</span>`;
    const ymd = ts => new Date(ts * 1000).toISOString().slice(0, 10);
    document.getElementById('bandDates').textContent =
      'published ' + ymd(st.first_pub) + ' → ' + ymd(st.last_pub) + ' · hover a frame for the talk';
    document.getElementById('footStamp').textContent =
      'corpus read ' + ymd(st.first_pub) + ' → ' + ymd(st.last_pub) + ' · ' +
      num(st.keyframes) + ' keyframes on disk';
  }

  /* ═══ beat 4 — the booth log types its question when you reach it ═══ */
  {
    const term = document.getElementById('term'), tq = document.getElementById('termQ');
    const QTXT = 'What do speakers disagree about when it comes to LLM as a judge?';
    const finish = () => { tq.textContent = QTXT; term.classList.remove('veiled', 'typingq'); };
    if (reduce || !('IntersectionObserver' in window)) finish();
    else {
      const io = new IntersectionObserver(async es => {
        if (!es.some(e => e.isIntersecting)) return;
        io.disconnect();
        term.classList.add('typingq');
        for (let i = 0; i < QTXT.length; i++) {
          tq.textContent = QTXT.slice(0, i + 1);
          await sleep(22 + (QTXT[i] === ' ' ? 12 : 0));
        }
        term.classList.remove('typingq');
        term.classList.remove('veiled');
      }, { threshold: .22 });
      io.observe(term);
    }
  }

  /* copy buttons */
  document.querySelectorAll('[data-copy]').forEach(b => {
    b.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); } catch (e) { /* no clipboard, no drama */ }
      const was = b.textContent; b.textContent = 'copied';
      setTimeout(() => { b.textContent = was; }, 1400);
    });
  });
})();
