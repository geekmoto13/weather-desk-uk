/* All libraries are bundled locally; no CDN scripts or native Python modules. */
"use strict";
importScripts("/h5wasm.js", "/lightning-data.js");
const decoded = new Map();
let queued = null,
  running = false;
self.onmessage = (event) => {
  queued = event.data;
  if (!running) drain();
};
async function readFile(row, location) {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 35000);
  let file = null;
  const name = `/flash-${row.id}.nc`;
  const { FS } = await h5wasm.ready;
  try {
    const response = await fetch(
      `/api/lightning/file?id=${encodeURIComponent(row.id)}`,
      { signal: controller.signal, cache: "no-store" },
    );
    if (!response.ok) {
      let message = "Satellite file unavailable. Retrying automatically.";
      try {
        const data = await response.json();
        if (typeof data.error === "string") message = data.error.slice(0, 250);
      } catch {}
      throw Error(message);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.length > 32 * 1024 * 1024)
      throw Error("Satellite file too large");
    FS.writeFile(name, bytes);
    file = new h5wasm.File(name, "r");
    return WeatherLightningData.decode(file, row, location);
  } finally {
    clearTimeout(timer);
    if (file) file.close();
    try {
      FS.unlink(name);
    } catch {}
  }
}
async function drain() {
  running = true;
  try {
    await h5wasm.ready;
    while (queued) {
      const job = queued;
      queued = null;
      const catalogue = job.catalogue;
      if (
        !catalogue ||
        !Array.isArray(catalogue.files) ||
        catalogue.files.length > 160
      )
        throw Error("Bad catalogue");
      const wanted = new Set(catalogue.files.map((r) => r.id));
      for (const id of decoded.keys()) if (!wanted.has(id)) decoded.delete(id);
      const pending = catalogue.files.filter((row) => !decoded.has(row.id));
      let failures = 0,
        fileError = null;
      const report = () =>
        self.postMessage({
          epoch: job.epoch,
          catalogueId: job.catalogueId,
          ...WeatherLightningData.summarize(decoded, catalogue),
          failures,
          fileError,
        });
      report();
      for (let index = 0; index < pending.length; index += 3) {
        if (queued) break;
        await Promise.all(
          pending.slice(index, index + 3).map(async (row) => {
            if (!/^[a-f0-9]{32}$/.test(row.id)) {
              failures++;
              return;
            }
            try {
              decoded.set(row.id, await readFile(row, job.location));
            } catch (error) {
              failures++;
              fileError =
                error.message || "A satellite file could not be decoded.";
            }
          }),
        );
        report();
      }
    }
  } catch {
    self.postMessage({
      fatal: true,
      error:
        "The bundled satellite decoder could not start. Reload the page or use the live map.",
    });
  } finally {
    running = false;
  }
}
