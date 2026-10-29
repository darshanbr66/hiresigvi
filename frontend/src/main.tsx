import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
import "./index.css";
import "./styles/motion.css";
import "./styles/shell.css";
import "./styles/auth.css";
import "./styles/public.css";
import "./styles/polish.css";
import "./styles/filters.css";
import "./styles/dashboard.css";
import "./styles/landing.css";
import "./styles/home.css";
import "./styles/loading.css";
import { initBrandLoading } from "./shared/lib/brandLoading";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found.");
}

initBrandLoading();

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
