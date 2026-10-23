const horizon = document.querySelector("#time-horizon");
const valueLabel = document.querySelector(".range-value");
const chatForm = document.querySelector("#chat-form");
const chatInput = document.querySelector("#chat-message");
const conversation = document.querySelector(".conversation");
const chatStatus = document.querySelector("#chat-status");
const taxForm = document.querySelector("#tax-form");
const taxInput = document.querySelector("#tax-message");
const taxConversation = document.querySelector("#tax-conversation");
const taxStatus = document.querySelector("#tax-status");
const createHandoffButton = document.querySelector("#create-handoff");
const handoffStatus = document.querySelector("#handoff-status");
const handoffOutput = document.querySelector("#handoff-output");
const handoffJson = document.querySelector("#handoff-json");
const copyHandoffButton = document.querySelector("#copy-handoff");

if (horizon && valueLabel) {
  function updateHorizon() {
    const value = Number(horizon.value);
    const min = Number(horizon.min);
    const max = Number(horizon.max);
    const progress = ((value - min) / (max - min)) * 100;

    valueLabel.textContent = value;
    horizon.setAttribute("aria-valuetext", `${value} years`);
    horizon.style.setProperty("--range-progress", `${progress}%`);
  }

  horizon.addEventListener("input", updateHorizon);
  updateHorizon();
}

const chatHistory = [];

function appendChatMessage(role, content, extraClass = "", target = conversation, assistantName = "Imperial assistant") {
  const message = document.createElement("div");
  message.className = `chat-turn ${role}-turn ${extraClass}`.trim();

  const speaker = document.createElement("span");
  speaker.className = "chat-speaker";
  speaker.textContent = role === "user" ? "You" : assistantName;

  const body = document.createElement("p");
  body.className = "chat-bubble";
  body.textContent = content;

  message.append(speaker, body);
  target.append(message);
  target.scrollTop = target.scrollHeight;
  return message;
}

function errorText(payload) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (payload?.detail) return JSON.stringify(payload.detail);
  return "The assistant could not complete that request. Please try again.";
}

if (chatForm && chatInput && conversation) {
  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;

    const sendButton = chatForm.querySelector(".send-button");
    appendChatMessage("user", message);
    chatInput.value = "";
    chatInput.style.height = "";
    sendButton.disabled = true;
    chatStatus.textContent = "Thinking…";
    const pending = appendChatMessage("assistant", "Thinking…", "pending-turn");

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          history: chatHistory,
          time_horizon: Number(horizon.value),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(errorText(payload));

      pending.remove();
      appendChatMessage("assistant", payload.reply);
      chatHistory.push({ role: "user", content: message });
      chatHistory.push({ role: "assistant", content: payload.reply });
      if (chatHistory.length > 30) chatHistory.splice(0, chatHistory.length - 30);
      chatStatus.textContent = "Reply received";
    } catch (error) {
      pending.remove();
      appendChatMessage("assistant", error.message, "error-turn");
      chatStatus.textContent = "Could not reach the assistant";
    } finally {
      sendButton.disabled = false;
      chatInput.focus();
    }
  });
}

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!chatInput) return;
    chatInput.value = button.dataset.prompt;
    chatInput.focus();
    chatInput.dispatchEvent(new Event("input", { bubbles: true }));
  });
});

const taxHistory = [];

if (taxForm && taxInput && taxConversation) {
  taxForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = taxInput.value.trim();
    if (!message) return;

    const sendButton = taxForm.querySelector(".send-button");
    appendChatMessage("user", message, "", taxConversation);
    taxInput.value = "";
    taxInput.style.height = "";
    sendButton.disabled = true;
    taxStatus.textContent = "Thinking…";
    const pending = appendChatMessage("assistant", "Thinking…", "pending-turn", taxConversation, "UK tax assistant");

    try {
      const response = await fetch("/api/tax", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          history: taxHistory,
          time_horizon: Number(horizon.value),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(errorText(payload));

      pending.remove();
      appendChatMessage("assistant", payload.reply, "", taxConversation, "UK tax assistant");
      taxHistory.push({ role: "user", content: message });
      taxHistory.push({ role: "assistant", content: payload.reply });
      if (taxHistory.length > 30) taxHistory.splice(0, taxHistory.length - 30);
      taxStatus.textContent = "Reply received";
    } catch (error) {
      pending.remove();
      appendChatMessage("assistant", error.message, "error-turn", taxConversation, "UK tax assistant");
      taxStatus.textContent = "Could not reach the assistant";
    } finally {
      sendButton.disabled = false;
      taxInput.focus();
    }
  });
}

for (const input of [chatInput, taxInput]) {
  if (!input) continue;
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 130)}px`;
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      input.closest("form")?.requestSubmit();
    }
  });
}

if (createHandoffButton && handoffStatus) {
  createHandoffButton.addEventListener("click", async () => {
    if (!chatHistory.some((turn) => turn.role === "assistant")) {
      handoffStatus.textContent = "Start and review the investor conversation before creating a handoff.";
      return;
    }

    createHandoffButton.disabled = true;
    copyHandoffButton.disabled = true;
    handoffStatus.textContent = "Preparing the portfolio builder brief…";
    try {
      const response = await fetch("/api/portfolio-brief", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          investor_history: chatHistory,
          tax_history: taxHistory,
          time_horizon: Number(horizon.value),
        }),
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(errorText(payload));
      }

      const packageData = await response.json();
      handoffJson.textContent = JSON.stringify(packageData, null, 2);
      handoffOutput.hidden = false;
      copyHandoffButton.disabled = false;
      handoffStatus.textContent = "JSON handoff prepared by the app function. Review it, then copy it for the portfolio builder.";
      window.dispatchEvent(new CustomEvent("imperial:portfolio-brief-ready", { detail: packageData }));
    } catch (error) {
      handoffStatus.textContent = error.message;
    } finally {
      createHandoffButton.disabled = false;
    }
  });
}

if (copyHandoffButton && handoffJson && handoffStatus) {
  copyHandoffButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(handoffJson.textContent);
      handoffStatus.textContent = "JSON payload copied to clipboard.";
    } catch {
      handoffStatus.textContent = "Clipboard access failed. Select and copy the JSON payload above.";
    }
  });
}
