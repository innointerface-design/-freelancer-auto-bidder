"use strict";

const form = document.getElementById("login-form");
const password = document.getElementById("password");
const toggle = document.getElementById("toggle-password");
const error = document.getElementById("login-error");
const button = document.getElementById("login-submit");

toggle.addEventListener("click", () => {
  const visible = password.type === "text";
  password.type = visible ? "password" : "text";
  toggle.textContent = visible ? "Show" : "Hide";
  toggle.setAttribute("aria-label", visible ? "Show password" : "Hide password");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.classList.add("hidden");
  button.disabled = true;
  button.textContent = "Signing in…";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-Login-Intent": "admin"},
      body: JSON.stringify({username: document.getElementById("username").value, password: password.value})
    });
    let result = {};
    try { result = await response.json(); } catch (_) { /* Generic message below. */ }
    if (!response.ok) {
      const detail = typeof result.detail === "string" ? result.detail : "Sign-in was unsuccessful. Check your details and try again.";
      throw new Error(response.status === 429 ? "Too many sign-in attempts. Please wait before trying again." : detail);
    }
    password.value = "";
    window.location.assign("/");
  } catch (failure) {
    error.textContent = failure.message || "Unable to connect. Please try again.";
    error.classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.textContent = "Sign in to workspace →";
  }
});
