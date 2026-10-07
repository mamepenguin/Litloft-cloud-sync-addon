import { lazy } from "react";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const slotComponents: Record<string, React.LazyExoticComponent<React.ComponentType<any>>> = {
  "cloud-sync": lazy(() => import("./CloudSyncWidget")),
  "cloud-sync-settings": lazy(() => import("./CloudSyncSettings")),
};
