import { useEffect, useState } from "react";

import { getComponents } from "./endpoints";
import type { Manifest } from "./types";

/** Fetch the component palette once (for the synthesizer's dropdowns). */
export function useManifest(): Manifest | null {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  useEffect(() => {
    let on = true;
    void getComponents()
      .then((m) => {
        if (on) setManifest(m);
      })
      .catch(() => {});
    return () => {
      on = false;
    };
  }, []);
  return manifest;
}
