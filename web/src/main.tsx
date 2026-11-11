import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./ui/fonts.css";
import "./ui/tokens.css";
import "./ui/base.css";
import { App } from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
