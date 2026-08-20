import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { App } from "./tga3/App";
import "./tga3/styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 500 } },
});

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter><QueryClientProvider client={queryClient}><App /></QueryClientProvider></BrowserRouter>
  </React.StrictMode>,
);
