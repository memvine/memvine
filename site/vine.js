/* The vine. A trailing stem hangs from the top edge of the page and grows as the reader scrolls;
   it never shrinks. Leaves unfurl as the tip passes and keep maturing: pale when new, deeper
   green with age. The tip glows faintly and leans toward the pointer. Drawn on a fixed canvas in
   document coordinates, so the vine belongs to the page, not the viewport. */
(() => {
  const canvas = document.getElementById("vine");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = matchMedia("(pointer: fine)").matches;

  const STEM_BASE = [62, 140, 94];     // mature stem
  const STEM_TIP = [168, 228, 191];    // new growth
  const LEAF_OLD = [38, 104, 68];
  const LEAF_NEW = [176, 230, 196];
  const GLOW = [216, 245, 227];

  let W = 0, H = 0, DOC = 0, scale = 1;
  let pts = [];          // sampled stem: {x, y, s (arc length), t (tangent angle)}
  let total = 0;         // full stem length
  let leaves = [];       // {s, side, size, tilt, curl}
  let grown = 0;         // length drawn now
  let target = 0;        // length the reader has earned (monotonic)
  let lastTime = 0;
  const startedAt = performance.now();
  let pointer = null;    // document coordinates
  let pointerSmooth = null;

  const rng = (seed) => () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const mix = (c0, c1, t, a) => {
    const r = Math.round(lerp(c0[0], c1[0], t)), g = Math.round(lerp(c0[1], c1[1], t)), b = Math.round(lerp(c0[2], c1[2], t));
    return `rgba(${r},${g},${b},${a})`;
  };
  const easeOutBack = (t) => { const c = 1.4; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };

  function build() {
    W = innerWidth;
    H = innerHeight;
    DOC = Math.max(document.documentElement.scrollHeight, H);
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    scale = clamp(W / 1280, 0.5, 1);
    const phone = W < 720;

    const r = rng(7);
    const axis = phone ? W - 18 : W < 1100 ? W * 0.76 : W * 0.7;
    const amp = phone ? 12 : 72;
    // The stem hangs from the right margin, clear of the header, and drifts to its axis.
    const header = document.querySelector(".top");
    const gutter = header ? parseFloat(getComputedStyle(header).paddingRight) || 24 : 24;
    const topX = phone ? axis : W - gutter * 0.5;
    const way = [{ x: topX, y: -60 }, { x: topX, y: 120 }];
    let y = 120, i = 0;
    while (y < DOC + 80) {
      y += 230 + r() * 130;
      i++;
      const settle = Math.min(1, i / 2.5);
      way.push({ x: lerp(topX, axis, settle) + (Math.sin(i * 1.05) * amp + (r() - 0.5) * amp * 0.9) * (0.5 + 0.5 * settle), y });
    }

    pts = [];
    let s = 0;
    for (let k = 0; k < way.length - 1; k++) {
      const p0 = way[Math.max(k - 1, 0)], p1 = way[k], p2 = way[k + 1], p3 = way[Math.min(k + 2, way.length - 1)];
      const n = 28;
      for (let j = 0; j < n; j++) {
        const t = j / n, t2 = t * t, t3 = t2 * t;
        const x = 0.5 * (2 * p1.x + (-p0.x + p2.x) * t + (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2 + (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3);
        const yy = 0.5 * (2 * p1.y + (-p0.y + p2.y) * t + (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2 + (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3);
        if (pts.length) { const q = pts[pts.length - 1]; s += Math.hypot(x - q.x, yy - q.y); }
        pts.push({ x, y: yy, s, t: 0 });
      }
    }
    total = s;
    for (let k = 0; k < pts.length; k++) {
      const a = pts[Math.max(k - 1, 0)], b = pts[Math.min(k + 1, pts.length - 1)];
      pts[k].t = Math.atan2(b.y - a.y, b.x - a.x);
    }

    const r2 = rng(21);
    leaves = [];
    let ls = 260 * scale, side = 1;
    while (ls < total - 120) {
      // A leaf near the right edge opens to the left, so none is cut off; on a phone that is all of them.
      const edge = phone || pointAt(ls).x > W - 150 * scale;
      leaves.push({ s: ls, side: edge ? -1 : side, size: (0.78 + r2() * 0.5) * scale * (phone ? 0.8 : 1), tilt: (r2() - 0.5) * (phone ? 0.9 : 0.5), curl: !phone && r2() > 0.62 });
      side = -side;
      ls += (150 + r2() * 110) * scale;
    }
  }

  function arcAtY(y) {
    for (let k = 0; k < pts.length; k++) if (pts[k].y >= y) return pts[k].s;
    return total;
  }

  function pointAt(s) {
    let lo = 0, hi = pts.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (pts[m].s < s) lo = m + 1; else hi = m; }
    const b = pts[lo], a = pts[Math.max(lo - 1, 0)];
    const span = b.s - a.s || 1;
    const t = clamp((s - a.s) / span, 0, 1);
    return { x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), t: b.t };
  }

  // Sway: the free end moves most. Tropism: the last stretch leans toward a nearby pointer.
  function offsetAt(s, now) {
    let dx = 0, dy = 0;
    if (!reduceMotion) {
      const free = clamp(s / Math.max(grown, 1), 0, 1);
      dx += Math.sin(now * 0.00085 + s * 0.0055) * 7 * free * free;
      dy += Math.sin(now * 0.00062 + s * 0.004) * 2.5 * free;
    }
    if (pointerSmooth && grown > 0) {
      const reach = 260 * scale;
      if (s > grown - reach) {
        const tip = pointAt(grown);
        const ddx = pointerSmooth.x - tip.x, ddy = pointerSmooth.y - tip.y;
        const dist = Math.hypot(ddx, ddy);
        const near = clamp(1 - dist / 360, 0, 1);
        const w = Math.pow((s - (grown - reach)) / reach, 2);
        const push = Math.min(dist, 28) * near * w;
        if (dist > 0.5) { dx += (ddx / dist) * push; dy += (ddy / dist) * push; }
      }
    }
    return { dx, dy };
  }

  function drawStem(now, top, bottom) {
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    let prev = null;
    for (let k = 0; k < pts.length; k++) {
      const p = pts[k];
      if (p.s > grown) break;
      const o = offsetAt(p.s, now);
      const cur = { x: p.x + o.dx, y: p.y + o.dy - scrollY };
      if (prev && (cur.y > top - 40 || prev.y > top - 40) && (cur.y < bottom + 40 || prev.y < bottom + 40)) {
        const f = p.s / grown;
        ctx.strokeStyle = mix(STEM_BASE, STEM_TIP, Math.pow(f, 1.4), 0.95);
        ctx.lineWidth = (0.9 + 2.6 * Math.pow(1 - f, 0.85)) * scale * (0.85 + 0.15 * Math.min(1, grown / 2400));
        ctx.beginPath();
        ctx.moveTo(prev.x, prev.y);
        ctx.lineTo(cur.x, cur.y);
        ctx.stroke();
      }
      prev = cur;
    }
    // The last stretch, to the exact tip.
    const tipP = pointAt(grown);
    const o = offsetAt(grown, now);
    const tip = { x: tipP.x + o.dx, y: tipP.y + o.dy - scrollY };
    if (prev) {
      ctx.strokeStyle = mix(STEM_BASE, STEM_TIP, 1, 0.95);
      ctx.lineWidth = 0.9 * scale;
      ctx.beginPath(); ctx.moveTo(prev.x, prev.y); ctx.lineTo(tip.x, tip.y); ctx.stroke();
    }
    return tip;
  }

  function drawGlow(tip) {
    if (tip.y < -80 || tip.y > H + 80) return;
    const r = 54 * scale;
    const g = ctx.createRadialGradient(tip.x, tip.y, 0, tip.x, tip.y, r);
    g.addColorStop(0, mix(GLOW, GLOW, 0, 0.30));
    g.addColorStop(0.45, mix(GLOW, GLOW, 0, 0.07));
    g.addColorStop(1, mix(GLOW, GLOW, 0, 0));
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(tip.x, tip.y, r, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = mix(GLOW, GLOW, 0, 0.9);
    ctx.beginPath(); ctx.arc(tip.x, tip.y, 1.5 * scale, 0, Math.PI * 2); ctx.fill();
  }

  function drawLeaf(leaf, now) {
    if (leaf.s > grown) return;
    const p = pointAt(leaf.s);
    const o = offsetAt(leaf.s, now);
    const x = p.x + o.dx, y = p.y + o.dy - scrollY;
    const age = grown - leaf.s;
    const unfurl = easeOutBack(clamp(age / (300 * scale), 0, 1));           // opens as the tip moves on
    const mature = clamp(age / 2600, 0, 1);                                   // then deepens with time
    const size = leaf.size * (0.82 + 0.18 * mature);
    const L = 96 * size, Wd = 46 * size;
    if (y < -L - 40 || y > H + L + 40) return;

    const open = p.t - leaf.side * (Math.PI / 2 - 0.42) + leaf.tilt;          // outward, drooping a little
    const folded = p.t + leaf.side * 0.18;                                    // along the stem before it opens
    const angle = lerp(folded, open, unfurl) + (reduceMotion ? 0 : Math.sin(now * 0.0011 + leaf.s) * 0.035);

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    const k = Math.max(unfurl, 0.001);
    ctx.scale(k, k);

    // petiole
    ctx.strokeStyle = mix(STEM_BASE, STEM_TIP, 1 - mature, 0.9);
    ctx.lineWidth = 1.1 * scale;
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.quadraticCurveTo(7 * size, 2 * size, 15 * size, 0); ctx.stroke();

    ctx.translate(15 * size, 0);
    const fill = ctx.createLinearGradient(0, 0, L, 0);
    fill.addColorStop(0, mix(LEAF_OLD, LEAF_NEW, 1 - mature, 0.92));
    fill.addColorStop(1, mix(LEAF_OLD, LEAF_NEW, Math.min(1, 1.25 - mature), 0.88));
    ctx.fillStyle = fill;
    ctx.strokeStyle = mix(STEM_TIP, GLOW, 0.3, 0.42);
    ctx.lineWidth = 0.8 * scale;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.bezierCurveTo(L * 0.22, -Wd * 0.6, L * 0.74, -Wd * 0.46, L, 0);
    ctx.bezierCurveTo(L * 0.74, Wd * 0.46, L * 0.22, Wd * 0.6, 0, 0);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // midrib and veins
    ctx.strokeStyle = mix(GLOW, GLOW, 0, 0.32 + 0.1 * mature);
    ctx.lineWidth = 0.75 * scale;
    ctx.beginPath(); ctx.moveTo(2 * size, 0); ctx.lineTo(L * 0.92, 0); ctx.stroke();
    ctx.lineWidth = 0.55 * scale;
    for (let i = 1; i <= 3; i++) {
      const vx = L * (0.18 + i * 0.19);
      const vl = Wd * (0.42 - i * 0.07);
      ctx.beginPath(); ctx.moveTo(vx, 0); ctx.quadraticCurveTo(vx + vl * 0.55, -vl * 0.5, vx + vl * 1.05, -vl * 0.72); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(vx, 0); ctx.quadraticCurveTo(vx + vl * 0.55, vl * 0.5, vx + vl * 1.05, vl * 0.72); ctx.stroke();
    }
    ctx.restore();

    // a tendril on the other side of some nodes
    if (leaf.curl) {
      const reach = clamp(age / (520 * scale), 0, 1);
      if (reach > 0) {
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(p.t + leaf.side * (Math.PI / 2 - 0.2));
        ctx.strokeStyle = mix(STEM_BASE, STEM_TIP, 0.7, 0.75);
        ctx.lineWidth = 0.8 * scale;
        ctx.beginPath();
        const turns = 2.6 * reach, R = 13 * size;
        for (let a = 0; a <= turns * Math.PI * 2; a += 0.15) {
          const rr = R * (1 - a / (2.6 * Math.PI * 2)) + 1.5;
          const px = 18 * size * Math.min(1, a / 2) + rr * Math.cos(a) - rr, py = rr * Math.sin(a);
          if (a === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.stroke();
        ctx.restore();
      }
    }
  }

  function frame(now) {
    const dt = Math.min(0.05, (now - (lastTime || now)) / 1000);
    lastTime = now;

    // What the reader has earned: the tip sits about three quarters of the way down the viewport.
    const earned = Math.min(total, arcAtY(scrollY + H * 0.78));
    if (now - startedAt > 450 || scrollY > 0) target = Math.max(target, earned);

    if (reduceMotion) grown = target;
    else {
      // Eases toward the target, never faster than a steady crawl, never slower than a trickle.
      const gap = target - grown;
      const step = Math.min(gap, Math.min(1400 * dt, Math.max(gap * 3 * dt, 90 * dt)));
      grown = Math.min(target, grown + step);
    }

    if (pointer) {
      pointerSmooth = pointerSmooth
        ? { x: lerp(pointerSmooth.x, pointer.x, 0.12), y: lerp(pointerSmooth.y, pointer.y, 0.12) }
        : { ...pointer };
    } else if (pointerSmooth) {
      pointerSmooth = null;
    }

    ctx.clearRect(0, 0, W, H);
    if (grown > 0) {
      const tip = drawStem(now, 0, H);
      for (const leaf of leaves) drawLeaf(leaf, now);
      drawGlow(tip);
    }
    if (!reduceMotion || grown < target) requestAnimationFrame(frame);
  }

  function rebuild() {
    const frac = total ? grown / total : 0;
    const tfrac = total ? target / total : 0;
    build();
    grown = frac * total;
    target = tfrac * total;
    if (reduceMotion) requestAnimationFrame(frame);
  }

  build();
  requestAnimationFrame(frame);
  addEventListener("resize", rebuild);
  addEventListener("load", rebuild);
  if ("ResizeObserver" in window) new ResizeObserver(() => { if (document.documentElement.scrollHeight !== DOC) rebuild(); }).observe(document.body);
  if (reduceMotion) addEventListener("scroll", () => requestAnimationFrame(frame), { passive: true });
  if (finePointer) {
    addEventListener("pointermove", (e) => { pointer = { x: e.clientX, y: e.clientY + scrollY }; }, { passive: true });
    document.addEventListener("pointerleave", () => { pointer = null; });
  }

  // The short timeline under "Teach one agent once" plays once, when it comes into view.
  const teach = document.querySelector(".teach");
  if (teach && "IntersectionObserver" in window) {
    teach.classList.add("ready");
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { teach.classList.add("shown"); io.disconnect(); }
    }, { threshold: 0.35 });
    io.observe(teach);
  }
  // "Copy prompt": the text of the element the button names goes to the clipboard.
  for (const button of document.querySelectorAll("[data-copy]")) {
    const label = button.textContent;
    button.addEventListener("click", async () => {
      const source = document.getElementById(button.dataset.copy);
      if (!source) return;
      try { await navigator.clipboard.writeText(source.textContent); button.textContent = button.dataset.copied || "Copied"; }
      catch { const d = source.closest("details"); if (d) d.open = true; getSelection().selectAllChildren(source); }
      setTimeout(() => { button.textContent = label; }, 1800);
    });
  }
})();
