const UX_UI_AUDITOR_LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", ""]);
const UX_UI_AUDITOR_BACKEND_OVERRIDE = window.localStorage.getItem("UX_UI_AUDITOR_API_BASE_URL") || "";
const UX_UI_AUDITOR_PUBLIC_BACKEND_URL = ["https://pretext", "deprive", "canine.ngrok-free.dev"].join("-");
const UX_UI_AUDITOR_PARAMS = new URLSearchParams(window.location.search);
const UX_UI_AUDITOR_QUERY_BACKEND = (
  UX_UI_AUDITOR_PARAMS.get("apiBaseUrl") ||
  UX_UI_AUDITOR_PARAMS.get("backend") ||
  UX_UI_AUDITOR_PARAMS.get("api") ||
  ""
).trim().replace(/\/+$/, "");

window.__UX_UI_AUDITOR_CONFIG__ = {
  // Leave empty only when the Python backend serves this same static folder.
  // Hosted static deployments use the public backend tunnel automatically.
  // Query-string and localStorage overrides take precedence over the default URL.
  apiBaseUrl:
    UX_UI_AUDITOR_QUERY_BACKEND ||
    UX_UI_AUDITOR_BACKEND_OVERRIDE ||
    (UX_UI_AUDITOR_LOCAL_HOSTS.has(window.location.hostname) ? "" : UX_UI_AUDITOR_PUBLIC_BACKEND_URL),
};
