"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { initializeBrowserOidc } from "@/lib/auth/browser";
import { makeQueryClient } from "@/lib/query-client";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => makeQueryClient());
  const [identityReady, setIdentityReady] = useState(false);
  const router = useRouter();
  useEffect(() => {
    let active = true;
    void initializeBrowserOidc()
      .then((initialization) => {
        if (active && initialization.authenticated) {
          if (initialization.returnTo) router.replace(initialization.returnTo);
          setIdentityReady(true);
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [router]);
  return (
    <QueryClientProvider client={queryClient}>
      {identityReady ? children : null}
    </QueryClientProvider>
  );
}
