const UX_UI_AUDITOR_LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", ""]);
const UX_UI_AUDITOR_BACKEND_OVERRIDE = window.localStorage.getItem("UX_UI_AUDITOR_API_BASE_URL") || "";

window.__UX_UI_AUDITOR_CONFIG__ = {
  // Leave empty only when the Python backend serves this same static folder.
  // Vercel deployments must point to the public backend tunnel.
  // You can override without editing this file from the browser console:
  // localStorage.setItem("UX_UI_AUDITOR_API_BASE_URL", "https://your-current-ngrok-url.ngrok-free.dev")
  apiBaseUrl:
    UX_UI_AUDITOR_BACKEND_OVERRIDE ||
    (UX_UI_AUDITOR_LOCAL_HOSTS.has(window.location.hostname) ? "" : "https://pretext-deprive-canine.ngrok-free.dev"),
};
