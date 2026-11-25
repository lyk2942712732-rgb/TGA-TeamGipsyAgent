import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { RuntimeApp } from "./app/RuntimeApp";
import { AppProviders } from "./app/providers";
import { applyNavMode, applyTheme, loadNavMode, loadTheme } from "./styles/theme";
import "./styles/globals.css";
import "./styles/reference.css";
import "./styles/tga3-runtime.css";
import "./styles/workbench.css";
import "./styles/collaboration.css";
import "./styles/aoe.css";
import "./styles/precision.css";

applyTheme(loadTheme());
applyNavMode(loadNavMode());

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter><AppProviders><RuntimeApp /></AppProviders></BrowserRouter>
  </React.StrictMode>,
);
