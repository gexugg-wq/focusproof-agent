"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { initializeBrowserOidc } from "@/lib/auth/browser";
import { makeQueryClient } from "@/lib/query-client";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => makeQueryClient());
  const [identityReady, setIdentityReady] = useState(false);
  useEffect(() => {
    let active = true;
    void initializeBrowserOidc()
      .then((ready) => {
        if (active && ready) setIdentityReady(true);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);
  return (
    <QueryClientProvider client={queryClient}>
      {identityReady ? children : null}
    </QueryClientProvider>
  );
}
