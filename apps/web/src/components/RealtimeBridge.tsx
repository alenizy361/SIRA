"use client";

import { useEffect } from "react";
import { getRealtimeClient } from "@/lib/ws";

/**
 * Mounted once inside the authenticated shell. Owns the lifetime of the
 * shared WebSocket connection to /ws so every page can read live state from
 * the zustand event store without each page opening its own socket.
 */
export function RealtimeBridge() {
  useEffect(() => {
    const client = getRealtimeClient();
    client.start();
    return () => client.stop();
  }, []);

  return null;
}
