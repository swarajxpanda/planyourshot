import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

// Optional error tracking, only when built with VITE_SENTRY_DSN. Loaded lazily so it costs
// nothing otherwise. Nothing personal is sent: no user info, cookies, headers, bodies,
// query strings or stack variables, and no console or input breadcrumbs.
const sentryDsn = import.meta.env.VITE_SENTRY_DSN as string | undefined;
if (sentryDsn) {
  import("@sentry/react").then((Sentry) => {
    Sentry.init({
      dsn: sentryDsn,
      environment: import.meta.env.MODE,
      dataCollection: { userInfo: false, cookies: false, httpHeaders: false, httpBodies: [], urlQueryParams: false, stackFrameVariables: false },
      integrations: [Sentry.breadcrumbsIntegration({ dom: false, xhr: true, fetch: true, history: true })],
      beforeBreadcrumb: (crumb) => (crumb.category === "console" || crumb.category?.startsWith("ui.") ? null : crumb),
      beforeSend: (event) => {
        if (event.request) {
          delete event.request.data;
          delete event.request.cookies;
          delete event.request.headers;
          delete event.request.query_string;
        }
        if (event.user) event.user = event.user.id ? { id: event.user.id } : {};
        delete event.extra;
        return event;
      },
    });
  }).catch(() => undefined);
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
