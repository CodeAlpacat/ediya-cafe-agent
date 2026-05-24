/* =============================================================
 *  Ediya · Local Order Demo — client logic
 *
 *  Modules:
 *    catalog   : 좌측 패널, /menu/catalog 1회 fetch + render
 *    transcript: 채팅, /chat 전송 + assistant 메시지 + debug panel
 *    order     : 우측 카트, /cart_action으로 수량/옵션/undo
 *    sync      : visual 액션 결과를 chat에 echo (system message)
 *    sheet     : 모바일 segmented tab
 *
 *  State machine for chat: idle → sending → waiting [→ slow] → idle | error
 *  No framework. ~450 lines.
 * ============================================================= */

(() => {
  "use strict";

  // ---------- constants ----------
  const SESSION_KEY = "ediya.session_id";
  const DEBUG_KEY   = "ediya.debug_mode";
  const SLOW_THRESHOLD_MS = 5000;
  const RESET_CONFIRM_TIMEOUT_MS = 4000;
  const TOAST_TIMEOUT_MS = 4500;

  // ---------- DOM ----------
  const $ = (id) => document.getElementById(id);
  const els = {
    transcript:       $("transcript"),
    welcome:          $("welcome"),
    suggestions:      $("suggestions"),
    input:            $("input"),
    sendBtn:          $("send"),
    composer:         $("composer"),
    resetBtn:         $("reset-btn"),
    debugBtn:         $("debug-btn"),
    statusPill:       $("status-pill"),
    statusText:       document.querySelector("#status-pill .status-text"),
    cartList:         $("cart-list"),
    cartEmpty:        $("cart-empty"),
    orderFoot:        $("order-foot"),
    lineCount:        $("line-count"),
    totalAmount:      $("total-amount"),
    undoBtn:          $("undo-btn"),
    orderMeta:        $("order-meta"),
    catalogScroll:    $("catalog-scroll"),
    catalogMeta:      $("catalog-meta"),
    segBadgeOrder:    $("seg-badge-order"),
    optionPopover:    $("option-popover"),
    optionPopoverTitle: $("option-popover-title"),
    optionPopoverBody:  $("option-popover-body"),
    optionPopoverClose: $("option-popover-close"),
    hoverPopover:     $("hover-popover"),
    toastHost:        $("toast-host"),
  };

  // ---------- state ----------
  let sessionId = localStorage.getItem(SESSION_KEY);
  let inFlight = false;
  let lastSentText = "";
  let prevCartKeys = new Set();
  let slowTimer = null;
  let resetConfirmTimer = null;
  let catalog = null;             // /menu/catalog JSON
  let menuByName = new Map();     // menu kr → {category, base_price_l, stock, tags}
  let menusInCart = new Set();    // 카탈로그 dot 표시용
  let activeOptionLine = null;    // 옵션 popover가 가리키는 카트 line

  // ---------- utils ----------
  const uid = () =>
    "session_" + (crypto.randomUUID?.() ?? Math.random().toString(36).slice(2)).replace(/-/g,"").slice(0,12);

  const ensureSession = () => {
    if (!sessionId) {
      sessionId = uid();
      localStorage.setItem(SESSION_KEY, sessionId);
    }
    return sessionId;
  };

  const formatKRW = (n) => (n ?? 0).toLocaleString("ko-KR") + "원";
  const formatDelta = (n) => (n > 0 ? `+${n.toLocaleString("ko-KR")}` : "");

  const scrollTranscript = () => {
    requestAnimationFrame(() => {
      els.transcript.scrollTop = els.transcript.scrollHeight;
    });
  };

  const cartKey = (it) => `${it.menu}|${(it.options||[]).slice().sort().join(",")}|${it.quantity}`;

  // ---------- status pill ----------
  function setStatus(state) {
    els.statusPill.dataset.state = state;
    const labels = {
      ready: "gemma4:e2b · 연결됨",
      busy:  "응답 작성 중",
      slow:  "조금 더 기다려주세요",
      error: "연결이 끊겼어요",
    };
    els.statusText.textContent = labels[state] || labels.ready;
  }

  // ---------- toast ----------
  function showToast(text, opts = {}) {
    const { kind = "info", actionLabel, onAction } = opts;
    const t = document.createElement("div");
    t.className = "toast";
    if (kind === "warn") t.dataset.kind = "warn";
    const span = document.createElement("span");
    span.textContent = text;
    t.appendChild(span);
    if (actionLabel) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = actionLabel;
      btn.addEventListener("click", () => { t.remove(); onAction && onAction(); });
      t.appendChild(btn);
    }
    els.toastHost.appendChild(t);
    setTimeout(() => {
      t.style.opacity = "0";
      t.style.transition = "opacity 280ms ease";
      setTimeout(() => t.remove(), 320);
    }, TOAST_TIMEOUT_MS);
  }

  // ---------- transcript ----------
  function dismissWelcome() {
    if (els.welcome && els.welcome.parentNode) els.welcome.remove();
    if (els.suggestions && !els.suggestions.hidden) els.suggestions.hidden = true;
  }

  function addMessage(role, text, opts = {}) {
    dismissWelcome();
    const msg = document.createElement("div");
    msg.className = `msg msg-${role}`;
    const roleLabels = { user: "나", assistant: "이디야", system: "시스템" };
    msg.innerHTML = `
      <div class="msg-role">${roleLabels[role] || role}</div>
      <div class="msg-body"></div>
    `;
    msg.querySelector(".msg-body").textContent = text;

    if (opts.toolCalls && opts.toolCalls.length > 0) {
      msg.appendChild(renderDebug(opts.toolCalls, opts.cartEvents || []));
    }

    els.transcript.appendChild(msg);
    scrollTranscript();
    return msg;
  }

  function renderDebug(toolCalls, cartEvents) {
    const wrap = document.createElement("div");
    wrap.className = "msg-debug";
    const isOn = document.body.dataset.debug === "1";
    if (isOn) wrap.dataset.open = "1";

    const summary = toolCalls.map(tc => `${tc.name}(${formatArgsInline(tc.arguments)})`).join(", ");

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "msg-debug-toggle";
    toggle.textContent = `tool_calls: ${summary}`;
    toggle.addEventListener("click", () => {
      wrap.dataset.open = wrap.dataset.open === "1" ? "0" : "1";
    });

    const body = document.createElement("div");
    body.className = "msg-debug-body";

    for (const tc of toolCalls) {
      const block = document.createElement("div");
      block.className = "dbg-tc";
      const status = tc.result?.status || "?";
      const bad = ["INVALID_MENU","INVALID_OPTION","AMBIGUOUS_MENU","SOLD_OUT","INSUFFICIENT_STOCK","MENU_NOT_IN_CART","NOTHING_TO_UNDO","UNKNOWN_TOOL"].includes(status);
      block.innerHTML = `
        <div><span class="dbg-tc-name">${tc.name}</span><span class="dbg-tc-status" data-bad="${bad ? "1":"0"}">${status}</span></div>
        <div class="dbg-section">args</div>
        <div>${escapeHtml(JSON.stringify(tc.arguments, null, 2))}</div>
        <div class="dbg-section">result</div>
        <div>${escapeHtml(JSON.stringify(tc.result, null, 2))}</div>
      `;
      body.appendChild(block);
    }

    if (cartEvents.length > 0) {
      const evBlock = document.createElement("div");
      evBlock.className = "dbg-tc";
      const lines = cartEvents.map(e => `• [${e.action}] ${e.summary}`).join("\n");
      evBlock.innerHTML = `
        <div class="dbg-section">cart events</div>
        <div>${escapeHtml(lines)}</div>
      `;
      body.appendChild(evBlock);
    }

    wrap.appendChild(toggle);
    wrap.appendChild(body);
    return wrap;
  }

  function formatArgsInline(args) {
    if (!args) return "";
    const parts = [];
    for (const [k, v] of Object.entries(args)) {
      let s;
      if (Array.isArray(v)) s = `[${v.join(",")}]`;
      else if (typeof v === "string") s = `"${v}"`;
      else s = String(v);
      parts.push(`${k}=${s}`);
    }
    return parts.join(", ");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function addThinking() {
    const wrap = document.createElement("div");
    wrap.className = "msg msg-assistant";
    wrap.id = "thinking-msg";
    wrap.innerHTML = `
      <div class="msg-role">이디야</div>
      <div class="msg-body">
        <span class="thinking">
          <span class="thinking-dots"><span></span><span></span><span></span></span>
          <span class="thinking-label">응답 작성 중</span>
        </span>
      </div>
    `;
    els.transcript.appendChild(wrap);
    scrollTranscript();
  }

  function updateThinkingLabel(text) {
    const t = document.querySelector("#thinking-msg .thinking-label");
    if (t) t.textContent = text;
  }

  function removeThinking() {
    const t = $("thinking-msg");
    if (t) t.remove();
  }

  // ---------- cart render ----------
  function renderCart(items, total) {
    items = items || [];
    total = total || 0;

    const newKeys = new Set(items.map(cartKey));
    els.cartList.innerHTML = "";
    menusInCart = new Set(items.map(it => it.menu));

    // catalog dot refresh
    refreshCatalogDots();

    if (items.length === 0) {
      els.cartEmpty.hidden = false;
      els.orderFoot.hidden = true;
      els.orderMeta.textContent = "";
      els.segBadgeOrder.hidden = true;
      els.undoBtn.disabled = true;
      prevCartKeys = newKeys;
      return;
    }

    els.cartEmpty.hidden = true;
    els.orderFoot.hidden = false;
    els.undoBtn.disabled = false;

    items.forEach((it) => {
      const li = document.createElement("li");
      li.className = "cart-line";
      const k = cartKey(it);
      if (prevCartKeys.size > 0 && !prevCartKeys.has(k)) li.classList.add("is-changed");

      // name
      const nameEl = document.createElement("div");
      nameEl.className = "cart-line-name";
      nameEl.textContent = it.menu;

      // qty controls
      const ctrl = document.createElement("div");
      ctrl.className = "cart-line-controls";
      ctrl.innerHTML = `
        <button class="qty-btn" data-act="dec" aria-label="수량 줄임">−</button>
        <span class="qty-display num">${it.quantity}</span>
        <button class="qty-btn" data-act="inc" aria-label="수량 늘림">＋</button>
        <button class="line-remove" data-act="remove" aria-label="이 메뉴 제거">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
            <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
          </svg>
        </button>
      `;
      ctrl.addEventListener("click", (e) => onCartLineControl(e, it));

      li.appendChild(nameEl);
      li.appendChild(ctrl);

      // options + picker button
      const optWrap = document.createElement("div");
      optWrap.className = "cart-line-options";
      optWrap.textContent = (it.options && it.options.length > 0)
        ? it.options.join(" · ")
        : "기본 옵션";
      const optBtn = document.createElement("button");
      optBtn.type = "button";
      optBtn.className = "cart-line-options-btn";
      optBtn.textContent = "옵션 변경";
      optBtn.addEventListener("click", (e) => openOptionPicker(e, it));
      optWrap.appendChild(optBtn);
      li.appendChild(optWrap);

      // price
      if (it.line_total != null) {
        const p = document.createElement("div");
        p.className = "cart-line-price num";
        p.textContent = formatKRW(it.line_total);
        li.appendChild(p);
      }

      els.cartList.appendChild(li);
    });

    const totalQty = items.reduce((s, it) => s + (it.quantity || 0), 0);
    els.orderMeta.textContent = `${items.length}종 · ${totalQty}잔`;
    els.lineCount.textContent = `${items.length}종 ${totalQty}잔`;
    els.totalAmount.textContent = formatKRW(total);
    els.segBadgeOrder.textContent = String(totalQty);
    els.segBadgeOrder.hidden = false;
    prevCartKeys = newKeys;
  }

  function onCartLineControl(e, it) {
    const btn = e.target.closest("button");
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === "inc") {
      cartAction("add_menu", { menu: it.menu, quantity: 1, options: it.options || [] });
    } else if (act === "dec") {
      cartAction("remove_menu", { menu: it.menu, quantity: 1 });
    } else if (act === "remove") {
      cartAction("remove_menu", { menu: it.menu, quantity: -1 });
    }
  }

  // ---------- catalog ----------
  async function loadCatalog() {
    try {
      const r = await fetch("/menu/catalog");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      catalog = await r.json();
      indexMenus();
      renderCatalog();
    } catch (err) {
      els.catalogScroll.innerHTML = `<div class="catalog-loading">메뉴를 불러오지 못했어요.</div>`;
      console.error(err);
    }
  }

  function indexMenus() {
    menuByName.clear();
    for (const cat of catalog.categories) {
      for (const m of cat.menus) {
        menuByName.set(m.kr, m);
      }
    }
  }

  function renderCatalog() {
    els.catalogScroll.innerHTML = "";
    const totalMenus = catalog.categories.reduce((s, c) => s + c.menus.length, 0);
    els.catalogMeta.textContent = `${totalMenus}종`;

    for (const cat of catalog.categories) {
      const group = document.createElement("details");
      group.className = "cat-group";
      if (cat.id === "coffee") group.open = true;  // 기본 펼침
      const desc = cat.descriptor || cat.id;
      group.innerHTML = `
        <summary>
          <span>${escapeHtml(desc)}</span>
          <span class="cat-group-count">${cat.menus.length}</span>
        </summary>
        <div class="cat-menus"></div>
      `;
      const list = group.querySelector(".cat-menus");
      for (const m of cat.menus) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "menu-row";
        row.dataset.menu = m.kr;
        row.dataset.category = m.category;
        const soldout = m.stock === 0;
        if (soldout) row.dataset.soldout = "1";
        row.innerHTML = `
          <span class="menu-name">${escapeHtml(m.kr)}${soldout ? '<span class="menu-soldout-tag">품절</span>' : ''}</span>
          <span class="menu-price">${m.base_price_l.toLocaleString("ko-KR")}</span>
        `;
        if (!soldout) {
          row.addEventListener("click", () => {
            cartAction("add_menu", { menu: m.kr, quantity: 1, options: [] });
            row.classList.remove("menu-pulse");
            void row.offsetWidth;  // restart animation
            row.classList.add("menu-pulse");
          });
          row.addEventListener("mouseenter", (e) => showHoverPopover(e, m));
          row.addEventListener("mouseleave", hideHoverPopover);
        }
        list.appendChild(row);
      }
      els.catalogScroll.appendChild(group);
    }
    refreshCatalogDots();
  }

  function refreshCatalogDots() {
    document.querySelectorAll(".menu-row").forEach(row => {
      const inCart = menusInCart.has(row.dataset.menu);
      row.dataset.inCart = inCart ? "1" : "0";
    });
  }

  function showHoverPopover(e, m) {
    if (!catalog) return;
    const applicable = catalog.option_categories.filter(oc => {
      if (!oc.applicable_categories) return true;
      return oc.applicable_categories.includes(m.category);
    });
    if (applicable.length === 0) {
      hideHoverPopover();
      return;
    }
    const opts = applicable.map(oc => oc.kr).join(" · ");
    els.hoverPopover.textContent = `가능 옵션: ${opts}`;
    els.hoverPopover.hidden = false;
    const rect = e.currentTarget.getBoundingClientRect();
    els.hoverPopover.style.left = `${rect.right + 8}px`;
    els.hoverPopover.style.top = `${rect.top}px`;
  }

  function hideHoverPopover() {
    els.hoverPopover.hidden = true;
  }

  // ---------- option picker popover ----------
  function openOptionPicker(e, line) {
    if (!catalog) return;
    activeOptionLine = line;
    const m = menuByName.get(line.menu);
    if (!m) return;

    els.optionPopoverTitle.textContent = line.menu;
    els.optionPopoverBody.innerHTML = "";

    const applicable = catalog.option_categories.filter(oc => {
      if (!oc.applicable_categories) return true;
      return oc.applicable_categories.includes(m.category);
    });

    const current = new Set(line.options || []);
    for (const oc of applicable) {
      const block = document.createElement("div");
      block.innerHTML = `<div class="opt-cat-label">${escapeHtml(oc.kr)}</div>`;
      const chips = document.createElement("div");
      chips.className = "opt-chips";
      for (const o of oc.options) {
        const isOn = current.has(o.kr);
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "opt-chip";
        if (isOn) chip.dataset.on = "1";
        chip.innerHTML = `
          ${escapeHtml(o.kr)}
          ${o.price_delta ? `<span class="opt-chip-delta">${formatDelta(o.price_delta)}</span>` : ""}
        `;
        chip.addEventListener("click", () => toggleOption(oc, o, line));
        chips.appendChild(chip);
      }
      block.appendChild(chips);
      els.optionPopoverBody.appendChild(block);
    }

    els.optionPopover.hidden = false;
    const rect = e.currentTarget.getBoundingClientRect();
    let left = rect.left - 280 + rect.width;
    let top = rect.bottom + 8;
    if (left < 8) left = 8;
    if (top + 380 > window.innerHeight) top = rect.top - 388;
    els.optionPopover.style.left = `${left}px`;
    els.optionPopover.style.top = `${top}px`;
  }

  function closeOptionPicker() {
    els.optionPopover.hidden = true;
    activeOptionLine = null;
  }

  els.optionPopoverClose.addEventListener("click", closeOptionPicker);

  function toggleOption(oc, opt, line) {
    const current = new Set(line.options || []);
    if (current.has(opt.kr)) {
      // off — 제거
      current.delete(opt.kr);
    } else {
      // on — 같은 카테고리가 exclusive면 같은 cat의 다른 옵션 제거
      if (oc.is_exclusive) {
        for (const other of oc.options) {
          if (other.kr !== opt.kr) current.delete(other.kr);
        }
      }
      current.add(opt.kr);
    }
    cartAction("change_option", {
      menu: line.menu,
      new_options: Array.from(current),
    });
    // 즉시 close — 응답 받으면 카트 갱신 + 다시 열고 싶으면 다시 클릭
    // 다만 사용자가 여러 옵션 한꺼번에 만지려 할 수 있으니 popover는 유지하고
    // 응답 후 새 line 정보로 갱신
  }

  // ---------- transport ----------
  async function postChat(message) {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: ensureSession(), message }),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new Error(`HTTP ${res.status} ${detail.slice(0, 120)}`);
    }
    return res.json();
  }

  async function postCartAction(action, args) {
    const res = await fetch("/cart_action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: ensureSession(), action, args }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async function postClear() {
    if (!sessionId) return;
    await fetch("/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
  }

  async function fetchCart() {
    if (!sessionId) return;
    try {
      const r = await fetch(`/cart?session_id=${encodeURIComponent(sessionId)}`);
      if (!r.ok) return;
      const data = await r.json();
      renderCart(data.cart, data.total);
    } catch (_) { /* noop */ }
  }

  // ---------- chat send flow ----------
  async function sendMessage(text) {
    if (inFlight || !text) return;
    inFlight = true;
    lastSentText = text;

    els.sendBtn.dataset.busy = "1";
    els.sendBtn.disabled = true;
    addMessage("user", text);
    els.input.value = "";
    autoResize();
    addThinking();
    setStatus("busy");

    clearTimeout(slowTimer);
    slowTimer = setTimeout(() => {
      setStatus("slow");
      updateThinkingLabel("조금 더 걸리고 있어요");
    }, SLOW_THRESHOLD_MS);

    try {
      const data = await postChat(text);
      removeThinking();
      addMessage("assistant", data.reply, {
        toolCalls: data.tool_calls,
        cartEvents: data.cart_events,
      });
      renderCart(data.cart, data.total);
      setStatus("ready");
    } catch (err) {
      removeThinking();
      setStatus("error");
      console.error(err);
      showToast("응답을 받지 못했어요.", {
        kind: "warn",
        actionLabel: "다시 시도",
        onAction: () => { setStatus("ready"); sendMessage(lastSentText); },
      });
    } finally {
      clearTimeout(slowTimer);
      inFlight = false;
      els.sendBtn.disabled = false;
      els.sendBtn.dataset.busy = "0";
      els.input.focus();
    }
  }

  // ---------- cart_action flow (visual UI → backend) ----------
  async function cartAction(action, args) {
    try {
      const data = await postCartAction(action, args);
      // chat에 system message로 echo
      addMessage("system", data.echo, {
        toolCalls: [data.tool_call],
        cartEvents: data.cart_events,
      });
      renderCart(data.cart, data.total);
      // 옵션 picker 열려 있으면 새 line으로 refresh
      if (activeOptionLine && data.cart) {
        const updated = data.cart.find(c => c.menu === activeOptionLine.menu);
        if (updated) activeOptionLine = updated;
        // 색상만 toggle — popover는 그대로
        const opts = new Set(updated ? updated.options : []);
        els.optionPopover.querySelectorAll(".opt-chip").forEach(chip => {
          const name = chip.firstChild?.textContent?.trim();
          chip.dataset.on = opts.has(name) ? "1" : "0";
        });
      }
      // 에러성 status면 warn toast
      const status = data.tool_call?.result?.status;
      if (status && /INVALID|AMBIGUOUS|SOLD_OUT|INSUFFICIENT_STOCK|NOTHING_TO_UNDO|MENU_NOT_IN_CART/.test(status)) {
        showToast(data.echo, { kind: "warn" });
      }
    } catch (err) {
      console.error(err);
      showToast("처리하지 못했어요. 잠시 후 다시 시도해 주세요.", { kind: "warn" });
    }
  }

  // ---------- composer ----------
  function autoResize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 140) + "px";
  }

  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = els.input.value.trim();
    if (text) sendMessage(text);
  });

  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      const text = els.input.value.trim();
      if (text) sendMessage(text);
    }
  });

  els.input.addEventListener("input", autoResize);

  // ---------- suggestions ----------
  els.suggestions.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const prompt = chip.dataset.prompt;
    if (prompt) sendMessage(prompt);
  });

  // ---------- reset (inline confirm) ----------
  function armResetConfirm() {
    els.resetBtn.dataset.confirm = "1";
    els.resetBtn.textContent = "정말 초기화?";
    clearTimeout(resetConfirmTimer);
    resetConfirmTimer = setTimeout(disarmResetConfirm, RESET_CONFIRM_TIMEOUT_MS);
  }

  function disarmResetConfirm() {
    els.resetBtn.dataset.confirm = "0";
    els.resetBtn.textContent = "대화 초기화";
    clearTimeout(resetConfirmTimer);
  }

  async function performReset() {
    try { await postClear(); } catch (_) {}
    sessionId = null;
    localStorage.removeItem(SESSION_KEY);
    els.transcript.innerHTML = "";
    const welcome = document.createElement("div");
    welcome.className = "welcome";
    welcome.id = "welcome";
    welcome.innerHTML = `
      <h2 class="welcome-title">무엇을 드릴까요?</h2>
      <p class="welcome-sub">초기화되었어요. 새로 주문이나 문의를 시작해 보세요.</p>
    `;
    els.transcript.appendChild(welcome);
    els.welcome = welcome;
    els.suggestions.hidden = false;
    renderCart([], 0);
    setStatus("ready");
    closeOptionPicker();
  }

  els.resetBtn.addEventListener("click", () => {
    if (els.resetBtn.dataset.confirm === "1") {
      disarmResetConfirm();
      performReset();
    } else {
      armResetConfirm();
    }
  });

  // ---------- debug toggle (global) ----------
  function setDebug(on) {
    document.body.dataset.debug = on ? "1" : "0";
    els.debugBtn.setAttribute("aria-pressed", on ? "true" : "false");
    localStorage.setItem(DEBUG_KEY, on ? "1" : "0");
  }

  els.debugBtn.addEventListener("click", () => {
    const cur = document.body.dataset.debug === "1";
    setDebug(!cur);
  });

  // ---------- undo button ----------
  els.undoBtn.addEventListener("click", () => {
    cartAction("undo", {});
  });

  // ---------- segmented tabs (mobile) ----------
  document.querySelectorAll(".seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      document.body.dataset.tab = target;
      document.querySelectorAll(".seg-btn").forEach(b => {
        const on = b.dataset.tab === target;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      document.querySelectorAll(".pane").forEach(p => {
        p.classList.toggle("is-active", p.dataset.pane === target);
      });
    });
  });

  // ---------- popover dismissal ----------
  document.addEventListener("click", (e) => {
    // 옵션 picker 외부 클릭으로 닫기
    if (!els.optionPopover.hidden) {
      const insidePopover = els.optionPopover.contains(e.target);
      const insideOptBtn = e.target.closest(".cart-line-options-btn");
      const insideOptChip = e.target.closest(".opt-chip");
      if (!insidePopover && !insideOptBtn && !insideOptChip) closeOptionPicker();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!els.optionPopover.hidden) closeOptionPicker();
      if (els.resetBtn.dataset.confirm === "1") disarmResetConfirm();
    }
  });

  // ---------- initial ----------
  setDebug(localStorage.getItem(DEBUG_KEY) === "1");
  setStatus("ready");
  // active pane: chat (HTML에 박힘) — 모바일에서만 토글
  document.querySelectorAll(".pane").forEach(p => {
    if (p.dataset.pane === "chat") p.classList.add("is-active");
  });
  loadCatalog();
  fetchCart();
})();
