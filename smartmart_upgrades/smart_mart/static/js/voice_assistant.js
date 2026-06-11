// smart_mart/static/js/voice_assistant.js
// ==========================================
// Voice assistant using browser Web Speech API — no extra libraries needed.
// Include on any page: <script src="{{ url_for('static', filename='js/voice_assistant.js') }}"></script>
// Add the button: <button id="voice-btn" title="Voice Assistant">🎤</button>

(function () {
  "use strict";

  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognition) {
    console.info("Voice assistant: SpeechRecognition not supported in this browser.");
    return;
  }

  // ── Create floating button ───────────────────────────────────────────────
  const btn = document.createElement("button");
  btn.id = "voice-fab";
  btn.title = "Voice Assistant";
  btn.innerHTML = "🎤";
  btn.style.cssText = `
    position: fixed; bottom: 24px; right: 24px;
    width: 52px; height: 52px; border-radius: 50%;
    background: #1A5C3A; color: white; border: none;
    font-size: 20px; cursor: pointer; z-index: 9999;
    box-shadow: 0 4px 12px rgba(0,0,0,.25);
    transition: background .2s, transform .1s;
  `;
  document.body.appendChild(btn);

  // ── Toast notification ───────────────────────────────────────────────────
  function showToast(msg, duration = 3000) {
    let toast = document.getElementById("voice-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "voice-toast";
      toast.style.cssText = `
        position: fixed; bottom: 90px; right: 24px;
        background: #333; color: #fff; padding: 10px 16px;
        border-radius: 8px; font-size: 14px; max-width: 280px;
        z-index: 9999; display: none; line-height: 1.4;
      `;
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.display = "block";
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toast.style.display = "none"; }, duration);
  }

  // ── Speak response using Web Speech Synthesis ────────────────────────────
  function speak(text) {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(text);
    utt.lang  = "en-IN";   // Indian English — closest available to Nepali accent
    utt.rate  = 0.95;
    window.speechSynthesis.speak(utt);
  }

  // ── Query the AI chat endpoint ────────────────────────────────────────────
  async function askAI(transcript) {
    showToast(`Heard: "${transcript}"\nAsking AI...`, 8000);
    try {
      const res  = await fetch("/ai/chat/ask", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ message: transcript, history: [] }),
      });
      const data = await res.json();
      const reply = data.reply || data.error || "Sorry, I could not get an answer.";
      showToast(reply, 8000);
      speak(reply);
    } catch (e) {
      showToast("Connection error. Is the AI chatbot enabled?", 4000);
    }
  }

  // ── Speech recognition ────────────────────────────────────────────────────
  const recognition = new SpeechRecognition();
  recognition.continuous    = false;
  recognition.interimResults= false;
  recognition.lang          = "en-IN";
  recognition.maxAlternatives = 1;

  let listening = false;

  recognition.onstart = () => {
    listening = true;
    btn.style.background = "#C9991A";
    btn.innerHTML = "🔴";
    showToast("Listening… speak your question", 10000);
  };

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    askAI(transcript);
  };

  recognition.onerror = (event) => {
    showToast(`Voice error: ${event.error}`, 3000);
  };

  recognition.onend = () => {
    listening = false;
    btn.style.background = "#1A5C3A";
    btn.innerHTML = "🎤";
  };

  btn.addEventListener("click", () => {
    if (listening) {
      recognition.stop();
    } else {
      recognition.start();
    }
  });

  // ── Suggested voice commands (shown on hover) ────────────────────────────
  btn.addEventListener("mouseenter", () => {
    showToast(
      "Try saying:\n• "How were sales today?"\n• "What's running low on stock?"\n• "What is my profit this month?"",
      5000,
    );
  });

})();
