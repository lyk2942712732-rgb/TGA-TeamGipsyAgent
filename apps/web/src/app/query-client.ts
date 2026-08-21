import { QueryClient } from "@tanstack/react-query";

/** Runtime events remain the sole live-state source; React Query caches only request responses. */
export const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 } } });
