import type { PropsWithChildren } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { GlobalUploadProgressBridge } from "../../components/app/GlobalUploadProgressBridge";
import { AuthModal } from "../../components/auth/AuthModal";
import { AuthProvider } from "../../hooks/useAuth";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

export function AppProviders({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        {children}
        <GlobalUploadProgressBridge />
        <AuthModal />
      </AuthProvider>
    </QueryClientProvider>
  );
}
