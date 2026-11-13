import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { RenderPage } from "./render";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>{location.pathname.startsWith("/render/") ? <RenderPage /> : <App />}</StrictMode>,
);
