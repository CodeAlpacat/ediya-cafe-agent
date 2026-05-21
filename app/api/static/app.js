(() => {
  const SESSION_KEY = "ediya.session_id";
  const $messages = document.getElementById("messages");
  const $input = document.getElementById("input");
  const $send = document.getElementById("send");
  const $composer = document.getElementById("composer");
  const $reset = document.getElementById("reset-btn");
  const $cartList = document.getElementById("cart-list");
  const $cartTotal = document.getElementById("cart-total");
  const $totalAmount = document.getElementById("total-amount");
  const $toastHost = document.getElementById("toast-host");

  let sessionId = localStorage.getItem(SESSION_KEY);
  let inFlight = false;

  // ---------- utils ----------

  function uid() {
    return "session_" + Math.random().toString(36).slice(2, 14);
  }

  function ensureSession() {
    if (!sessionId) {
      sessionId = uid();
      localStorage.setItem(SESSION_KEY, sessionId);
    }
    return sessionId;
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      $messages.scrollTop = $messages.scrollHeight;
    });
  }

  function addBubble(role, text) {
    const div = document.createElement("div");
    div.className = "bubble " + (role === "user" ? "bubble-user" : "bubble-bot");
    div.textContent = text;
    $messages.appendChild(div);
    scrollToBottom();
    return div;
  }

  function showTyping() {
    const div = document.createElement("div");
    div.className = "bubble-typing";
    div.innerHTML = "<span></span><span></span><span></span>";
    div.id = "typing-indicator";
    $messages.appendChild(div);
    scrollToBottom();
    return div;
  }

  function hideTyping() {
    const t = document.getElementById("typing-indicator");
    if (t) t.remove();
  }

  function showToast(text) {
    const t = document.createElement("div");
    t.className = "toast";
    t.textContent = text;
    $toastHost.appendChild(t);
    setTimeout(() => {
      t.style.opacity = "0";
      t.style.transition = "opacity 0.3s";
      setTimeout(() => t.remove(), 320);
    }, 3500);
  }

  function formatPrice(n) {
    if (!n && n !== 0) return "";
    return n.toLocaleString("ko-KR") + "원";
  }

  // ---------- cart render ----------

  function renderCart(items, total) {
    $cartList.innerHTML = "";

    if (!items || items.length === 0) {
      const empty = document.createElement("div");
      empty.className = "cart-empty";
      empty.innerHTML =
        '<p>아직 담긴 메뉴가 없어요</p><small>채팅으로 주문해 보세요</small>';
      $cartList.appendChild(empty);
      $cartTotal.classList.add("hidden");
      return;
    }

    for (const it of items) {
      const item = document.createElement("div");
      item.className = "cart-item";

      const name = document.createElement("div");
      name.className = "cart-item-name";
      name.textContent = it.menu;

      const qty = document.createElement("div");
      qty.className = "cart-item-qty";
      qty.textContent = "× " + it.quantity;

      item.appendChild(name);
      item.appendChild(qty);

      if (it.options && it.options.length > 0) {
        const opts = document.createElement("div");
        opts.className = "cart-item-options";
        opts.textContent = "옵션: " + it.options.join(", ");
        item.appendChild(opts);
      }

      if (it.line_total) {
        const lt = document.createElement("div");
        lt.className = "cart-item-line-total";
        lt.textContent = formatPrice(it.line_total);
        item.appendChild(lt);
      }

      $cartList.appendChild(item);
    }

    $totalAmount.textContent = formatPrice(total || 0);
    $cartTotal.classList.remove("hidden");
  }

  // ---------- API ----------

  async function postChat(message) {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: ensureSession(), message }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async function postClear() {
    const sid = sessionId;
    if (!sid) return;
    await fetch("/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid }),
    });
  }

  async function fetchCart() {
    if (!sessionId) return;
    try {
      const res = await fetch(
        `/cart?session_id=${encodeURIComponent(sessionId)}`
      );
      if (!res.ok) return;
      const data = await res.json();
      renderCart(data.cart, data.total);
    } catch (e) {
      // 무시 — 페이지 로드 시 카트가 비어도 OK
    }
  }

  // ---------- handlers ----------

  async function handleSend() {
    const text = $input.value.trim();
    if (!text || inFlight) return;

    inFlight = true;
    $send.disabled = true;
    addBubble("user", text);
    $input.value = "";
    autoResize();

    showTyping();
    try {
      const data = await postChat(text);
      hideTyping();
      addBubble("bot", data.reply);
      renderCart(data.cart, data.total);
    } catch (err) {
      hideTyping();
      showToast("서버와 연결이 끊겼어요. 다시 시도해 주세요.");
      console.error(err);
    } finally {
      inFlight = false;
      $send.disabled = false;
      $input.focus();
    }
  }

  async function handleReset() {
    if (!confirm("대화와 카트를 모두 초기화할까요?")) return;
    try {
      await postClear();
    } catch (e) {
      // 무시
    }
    sessionId = null;
    localStorage.removeItem(SESSION_KEY);
    $messages.innerHTML = "";
    addBubble(
      "bot",
      "초기화되었어요. 새로 주문을 시작해 주세요."
    );
    renderCart([], 0);
  }

  function autoResize() {
    $input.style.height = "auto";
    $input.style.height = Math.min($input.scrollHeight, 120) + "px";
  }

  // ---------- wire-up ----------

  $composer.addEventListener("submit", (e) => {
    e.preventDefault();
    handleSend();
  });

  $input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  $input.addEventListener("input", autoResize);
  $reset.addEventListener("click", handleReset);

  // 초기 로드: 이전 세션 카트 복원
  fetchCart();
})();
