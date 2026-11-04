/* RainViewer past radar over bundled public-domain Natural Earth geography. */
((root) => {
  "use strict";
  function create({ api, getSettings }) {
    const $ = (id) => document.getElementById(id),
      images = new Map(),
      requests = [];
    let frames = [],
      host = "",
      geography = null,
      index = 0,
      zoom = 7,
      timer = null,
      revision = 0,
      position = "",
      playing = false;
    const stamp = (t) =>
      new Intl.DateTimeFormat("en-GB", {
        timeZone: "Europe/London",
        hour: "2-digit",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(new Date(t * 1000));
    function tile(url, limited) {
      if (images.has(url)) return images.get(url);
      const job = new Promise((resolve, reject) => {
        function start() {
          const now = Date.now();
          while (requests.length && requests[0] < now - 60000) requests.shift();
          if (limited && requests.length >= 75) {
            setTimeout(start, Math.max(100, requests[0] + 60100 - now));
            return;
          }
          if (limited) requests.push(now);
          const img = new Image();
          img.alt = "";
          img.decoding = "async";
          img.onload = () => resolve(img);
          img.onerror = () =>
            reject(
              new Error(
                "Some map tiles could not load. The map may be incomplete.",
              ),
            );
          img.src = url;
        }
        start();
      });
      images.set(url, job);
      if (images.size > 400) images.delete(images.keys().next().value);
      return job;
    }
    async function draw() {
      const token = ++revision,
        s = getSettings(),
        frame = frames[index];
      if (!s || !frame) return;
      const box = $("ukRadarMap"),
        width = box.clientWidth,
        height = box.clientHeight,
        n = 2 ** zoom;
      const latitude =
        (Math.max(-85, Math.min(85, s.latitude)) * Math.PI) / 180;
      const cx = ((s.longitude + 180) / 360) * n * 256,
        cy =
          ((1 -
            Math.log(Math.tan(latitude) + 1 / Math.cos(latitude)) / Math.PI) /
            2) *
          n *
          256;
      const left = cx - width / 2,
        top = cy - height / 2,
        layer = document.createElement("div");
      layer.className = "uk-map-layer";
      const ns = "http://www.w3.org/2000/svg",
        svg = document.createElementNS(ns, "svg"),
        labels = document.createElementNS(ns, "svg");
      for (const node of [svg, labels]) {
        node.setAttribute("viewBox", `0 0 ${width} ${height}`);
        node.setAttribute("width", width);
        node.setAttribute("height", height);
        node.classList.add("uk-map-geography");
      }
      svg.style.zIndex = "1";
      labels.style.zIndex = "3";
      const project = ([lon, lat]) => {
        const phi = (Math.max(-85, Math.min(85, lat)) * Math.PI) / 180;
        return [
          ((lon + 180) / 360) * n * 256 - left,
          ((1 - Math.log(Math.tan(phi) + 1 / Math.cos(phi)) / Math.PI) / 2) *
            n *
            256 -
            top,
        ];
      };
      for (const polygon of geography?.polygons || []) {
        const path = document.createElementNS(ns, "path");
        path.setAttribute(
          "d",
          polygon
            .map(
              (ring) =>
                ring
                  .map((point, i) => {
                    const [x, y] = project(point);
                    return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
                  })
                  .join(" ") + "Z",
            )
            .join(" "),
        );
        path.setAttribute("fill-rule", "evenodd");
        path.setAttribute("fill", "#c5d1b7");
        path.setAttribute("stroke", "#829a8b");
        path.setAttribute("stroke-width", ".8");
        svg.append(path);
      }
      const occupied = [[width / 2, height / 2]];
      for (const place of geography?.places || []) {
        const [x, y] = project([place.longitude, place.latitude]);
        if (
          x < 12 ||
          x > width - 12 ||
          y < 15 ||
          y > height - 38 ||
          occupied.some(([px, py]) => Math.hypot(x - px, y - py) < 53)
        )
          continue;
        const dot = document.createElementNS(ns, "circle");
        dot.setAttribute("cx", x);
        dot.setAttribute("cy", y);
        dot.setAttribute("r", "2");
        dot.setAttribute("fill", "#23434b");
        labels.append(dot);
        const text = document.createElementNS(ns, "text");
        text.setAttribute("x", x + (x > width - 80 ? -5 : 5));
        text.setAttribute("y", y - 4);
        text.setAttribute("text-anchor", x > width - 80 ? "end" : "start");
        text.textContent = place.name;
        labels.append(text);
        occupied.push([x, y]);
      }
      const home = document.createElementNS(ns, "text");
      home.setAttribute("x", width / 2 + 8);
      home.setAttribute("y", height / 2 - 8);
      home.textContent = s.location.split(",")[0];
      home.classList.add("uk-map-home");
      labels.append(home);
      layer.append(svg, labels);
      const jobs = [];
      for (
        let x = Math.floor(left / 256);
        x <= Math.floor((left + width) / 256);
        x++
      )
        for (
          let y = Math.floor(top / 256);
          y <= Math.floor((top + height) / 256);
          y++
        ) {
          if (y < 0 || y >= n) continue;
          const tx = ((x % n) + n) % n;
          const url = `${host}${frame.path}/256/${zoom}/${tx}/${y}/2/1_1.png`;
          jobs.push(
            tile(url, true).then((source) => {
              const img = source.cloneNode();
              img.style.left = x * 256 - left + "px";
              img.style.top = y * 256 - top + "px";
              img.style.zIndex = 2;
              layer.append(img);
            }),
          );
        }
      const result = await Promise.allSettled(jobs);
      if (token !== revision) return;
      box.querySelector(".uk-map-layer")?.remove();
      box.prepend(layer);
      $("radarFrame").value = String(index);
      $("radarTimestamp").textContent =
        `Radar observed ${stamp(frame.time)} · ${index + 1}/${frames.length}`;
      $("radarNote").textContent = result.some((r) => r.status === "rejected")
        ? "Some radar/map tiles failed. The map may be incomplete; use the Met Office map link."
        : `${Date.now() / 1000 - frames[frames.length - 1].time > 1800 ? "RADAR UPDATES OVERDUE · " : ""}Past radar only · frames refresh every 5 minutes · no forecast echoes. Blue through yellow/red shows increasing reflectivity; echoes are separate from your gauge.`;
    }
    function pause() {
      playing = false;
      clearTimeout(timer);
      $("radarPlay").textContent = "▶ Play";
    }
    async function tick() {
      if (!playing) return;
      index = (index + 1) % frames.length;
      await draw();
      if (playing) timer = setTimeout(tick, 900);
    }
    async function refresh() {
      const irish = getSettings()?.countryCode === "IE";
      $("radarLink").href = irish
        ? "https://www.met.ie/"
        : "https://weather.metoffice.gov.uk/maps-and-charts/rainfall-radar-forecast-map";
      $("radarLink").textContent = irish
        ? "Met Éireann map ↗"
        : "Met Office map ↗";
      $("radarStationLabel").textContent = irish
        ? "IRELAND RAIN RADAR"
        : "UK RAIN RADAR";
      const s = getSettings();
      if (!s) return;
      const key = `${s.latitude},${s.longitude}`;
      if (key !== position) {
        position = key;
        pause();
        images.clear();
      }
      $("loadRadarBtn").disabled = true;
      try {
        const [raw, map] = await Promise.all([
          api("/api/radar"),
          geography ? Promise.resolve(geography) : api("/uk-map.json"),
        ]);
        geography = map;
        frames = raw.frames || [];
        host = raw.host;
        index = frames.length - 1;
        $("radarFrame").max = String(Math.max(0, index));
        await draw();
      } catch (error) {
        $("radarNote").textContent =
          "Radar could not refresh. Any displayed frame keeps its original observation time. Check the official weather map.";
      } finally {
        $("loadRadarBtn").disabled = false;
      }
    }
    $("radarPlay").addEventListener("click", () => {
      if (playing) pause();
      else if (frames.length) {
        playing = true;
        $("radarPlay").textContent = "❚❚ Pause";
        tick();
      }
    });
    $("radarFrame").addEventListener("input", (event) => {
      pause();
      index = Number(event.target.value);
      draw();
    });
    $("radarZoom").addEventListener("change", (event) => {
      pause();
      zoom = Number(event.target.value);
      draw();
    });
    let resize;
    new ResizeObserver(() => {
      clearTimeout(resize);
      resize = setTimeout(() => {
        if (frames.length) draw();
      }, 250);
    }).observe($("ukRadarMap"));
    return { refresh, pause };
  }
  root.WeatherUKRadar = { create };
})(globalThis);
