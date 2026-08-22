import type { PropsWithChildren } from "react";

import { AuthProvider } from "../../hooks/useAuth";
import { LocaleProvider } from "./LocaleProvider";
import { ThemeProvider } from "./ThemeProvider";

export function AppProviders({ children }: PropsWithChildren) {
  return (
    <LocaleProvider>
      <ThemeProvider>
        <AuthProvider>{children}</AuthProvider>
      </ThemeProvider>
    </LocaleProvider>
  );
}
